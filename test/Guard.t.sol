// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import {Vm} from "forge-std/Test.sol";
import {console} from "forge-std/console.sol";
import {AbstractPayer} from "@reactive/src/base/AbstractPayer.sol";
import {AbstractCallback} from "@reactive/src/base/AbstractCallback.sol";
import {IPayable} from "@reactive/src/interfaces/IPayable.sol";
import {IReactive} from "@reactive/src/interfaces/IReactive.sol";
import {ReflexGuardRC} from "../src/reactive/ReflexGuardRC.sol";
import {GuardedPool} from "../src/origin/GuardedPool.sol";
import {MockToken} from "../src/origin/MockToken.sol";
import {Base, MockSystem} from "./Helpers.sol";

/// @notice End-to-end behaviour with the real pool: swaps on the pool are relayed to the RC as the network
///         would, callbacks requested by the RC are executed against the pool as the proxy would.
contract GuardTest is Base {
    ReflexGuardRC internal rc;
    GuardedPool internal pool;
    MockToken internal token0;
    MockToken internal token1;

    string internal plan;
    uint256 internal clock = 1_760_000_000;
    uint256 internal nonce = 1;

    function setUp() public {
        _etchSystem();
        token0 = new MockToken("Token0", "TK0");
        token1 = new MockToken("Token1", "TK1");

        // The RC needs the pool address and the pool needs the RC address: predict the pool's CREATE address.
        address predicted = vm.computeCreateAddress(address(this), vm.getNonce(address(this)) + 1);
        rc = new ReflexGuardRC(predicted, CHAIN_ID, _threshold(), _weightWords());
        pool = new GuardedPool(address(token0), address(token1), _proxy(), address(rc), address(this));
        assertEq(address(pool), predicted, "pool address prediction");

        plan = vm.readFile("ml/demo_plan.json");
        uint256 liquidity = vm.parseUint(vm.parseJsonString(plan, ".liquidity"));
        token0.mint(address(this), liquidity * 100);
        token1.mint(address(this), liquidity * 100);
        token0.approve(address(pool), type(uint256).max);
        token1.approve(address(pool), type(uint256).max);
        pool.addLiquidity(liquidity, liquidity);
        vm.warp(clock);
    }

    function _proxy() internal pure returns (IPayable) {
        return IPayable(payable(PROXY));
    }

    /// @dev Execute one swap on the pool and relay its logs to the RC.
    function _swapAndRelay(bool zeroForOne, uint256 amountIn) internal {
        vm.recordLogs();
        pool.swap(zeroForOne, amountIn, 0, address(this));
        _relay(rc, address(pool), vm.getRecordedLogs(), nonce++);
    }

    /// @dev Play a phase of ml/demo_plan.json against the pool. Returns the highest score seen.
    function _play(string memory phase) internal returns (uint256 maxScore) {
        uint256 gap = vm.parseJsonUint(plan, ".gap");
        uint256[] memory dirs = vm.parseJsonUintArray(plan, string.concat(".", phase, ".zeroForOne"));
        string[] memory amounts = vm.parseJsonStringArray(plan, string.concat(".", phase, ".amountIn"));
        for (uint256 i; i < dirs.length; ++i) {
            clock += gap;
            vm.warp(clock);
            vm.recordLogs();
            pool.swap(dirs[i] == 1, vm.parseUint(amounts[i]), 0, address(this));
            Vm.Log[] memory logs = vm.getRecordedLogs();
            vm.recordLogs();
            _relay(rc, address(pool), logs, nonce++);
            Vm.Log[] memory rcLogs = vm.getRecordedLogs();
            for (uint256 j; j < rcLogs.length; ++j) {
                if (rcLogs[j].topics[0] == ReflexGuardRC.Scored.selector) {
                    (uint256 score,,,) = abi.decode(rcLogs[j].data, (uint256, uint256, uint256, uint256));
                    if (score > maxScore) maxScore = score;
                }
            }
        }
    }

    /// @dev Deliver the first requested callback to the pool, as the callback proxy would.
    function _deliverFirstCallback() internal returns (bytes memory payload) {
        MockSystem.Request memory req = _system().request(0);
        (, uint256 score, uint256 txHash) = abi.decode(_args(req.payload), (address, uint256, uint256));
        payload = abi.encodeCall(GuardedPool.haltPool, (address(rc), score, txHash));
        vm.prank(PROXY);
        (bool ok,) = req.recipient.call(payload);
        assertTrue(ok, "callback execution failed");
    }

    function _args(bytes memory payload) internal pure returns (bytes memory args) {
        args = new bytes(payload.length - 4);
        for (uint256 i; i < args.length; ++i) {
            args[i] = payload[i + 4];
        }
    }

    // ------------------------------------------------------------------ deployment

    function test_constructorSubscribesToSwapAndResumed() public view {
        assertEq(_system().subscriptions(), 2);
        assertEq(rc.pool(), address(pool));
        assertEq(pool._CALLBACK_SENDER(), address(rc));
    }

    // ------------------------------------------------------------------ benign flow

    function test_benignWhaleDoesNotHalt() public {
        uint256 maxScore = _play("warmup");
        console.log("warm-up + whale: highest score", maxScore, "threshold", _threshold());
        assertLt(maxScore, _threshold());
        assertEq(_system().requestCount(), 0, "benign flow must not request a callback");
        assertFalse(pool.paused());
    }

    // ------------------------------------------------------------------ attack: callback and halt

    function test_pumpAndDumpRequestsCallbackAndHaltsPool() public {
        _play("warmup");
        uint256 maxScore = _play("attack_a");
        assertGe(maxScore, _threshold());
        assertGe(_system().requestCount(), 1);

        MockSystem.Request memory req = _system().request(0);
        assertEq(req.chainId, CHAIN_ID);
        assertEq(req.recipient, address(pool));
        assertEq(req.gasLimit, rc.CALLBACK_GAS());
        assertGe(req.gasLimit, 100_000);
        assertEq(bytes4(req.payload), GuardedPool.haltPool.selector);
        (address injected, uint256 score,) = abi.decode(_args(req.payload), (address, uint256, uint256));
        assertEq(injected, address(0), "first argument is a placeholder the proxy overwrites");
        assertGe(score, _threshold());

        vm.recordLogs();
        _deliverFirstCallback();
        Vm.Log[] memory logs = vm.getRecordedLogs();
        assertEq(logs[0].topics[0], GuardedPool.Halted.selector);
        assertTrue(pool.paused());

        vm.expectRevert(GuardedPool.PoolPaused.selector);
        pool.swap(true, 1e18, 0, address(this));
    }

    function test_oneRequestPerHaltEvenIfSeveralLegsScoreHigh() public {
        _play("warmup");
        _play("attack_a");
        assertEq(_system().requestCount(), 1);
    }

    function test_haltRequestIsRepeatedAfterRetryWindowIfPoolStillTrades() public {
        _play("warmup");
        _play("attack_a");
        assertEq(_system().requestCount(), 1);

        // the callback never lands; a new large reversal arrives after the retry window
        vm.prank(rc.owner());
        rc.setThreshold(rc.MIN_THRESHOLD());
        clock += rc.RETRY_AFTER() + 1;
        vm.warp(clock);
        _swapAndRelay(false, 12_000e18);
        clock += 1;
        vm.warp(clock);
        _swapAndRelay(true, 9_000e18);
        assertEq(_system().requestCount(), 2, "second request after the retry window");
    }

    function test_dripAfterResumeIsHaltedAgain() public {
        _play("warmup");
        _play("attack_a");
        _deliverFirstCallback();
        assertTrue(pool.paused());
        uint256 firstRequests = _system().requestCount();

        // owner resumes; the Resumed log re-arms the guard
        vm.recordLogs();
        pool.resume();
        _relay(rc, address(pool), vm.getRecordedLogs(), nonce++);
        assertFalse(pool.paused());
        assertEq(rc.haltRequestedAt(), 0, "guard re-armed");
        assertEq(rc.flowState().priceEma, 0, "rolling state cleared");

        uint256 maxScore = _play("drip_c");
        assertGe(maxScore, _threshold());
        assertEq(_system().requestCount(), firstRequests + 1);
    }

    // ------------------------------------------------------------------ authorisation

    function test_haltPoolRejectsRandomCaller() public {
        address stranger = address(0xBAD);
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(AbstractPayer.NotAuthorized.selector, stranger, PROXY));
        pool.haltPool(address(rc), 10_000, 1);
        assertFalse(pool.paused());
    }

    function test_haltPoolRejectsRandomCallerEvenWithRightRcAddress() public {
        vm.prank(address(rc));
        vm.expectRevert(abi.encodeWithSelector(AbstractPayer.NotAuthorized.selector, address(rc), PROXY));
        pool.haltPool(address(rc), 10_000, 1);
    }

    function test_haltPoolRejectsProxyWithWrongInjectedSender() public {
        address other = address(0xDEAD);
        vm.prank(PROXY);
        vm.expectRevert(abi.encodeWithSelector(AbstractCallback.CallbackNotAuthorized.selector, other, address(rc)));
        pool.haltPool(other, 10_000, 1);
        assertFalse(pool.paused());
    }

    function test_haltPoolAcceptsProxyWithRcAddress() public {
        vm.prank(PROXY);
        pool.haltPool(address(rc), 9_000, 42);
        assertTrue(pool.paused());
    }

    function test_reactIsOnlySystem() public {
        IReactive.LogRecord memory log = _swapLog(address(pool), address(this), true, 1e18, 1e18, 1e24, 1e24, clock, 1);
        vm.expectRevert(abi.encodeWithSelector(AbstractPayer.NotAuthorized.selector, address(this), SYSTEM));
        rc.react(log);
    }

    function test_resumeIsOwnerOnly() public {
        vm.prank(address(0xBAD));
        vm.expectRevert(GuardedPool.NotOwner.selector);
        pool.resume();
    }

    function test_addLiquidityIsOwnerOnly() public {
        vm.prank(address(0xBAD));
        vm.expectRevert(GuardedPool.NotOwner.selector);
        pool.addLiquidity(1, 1);
    }

    function test_thresholdOnlyOwnerAndWithinBounds() public {
        uint256 min = rc.MIN_THRESHOLD();
        uint256 max = rc.MAX_THRESHOLD();

        vm.prank(address(0xBAD));
        vm.expectRevert(ReflexGuardRC.NotOwner.selector);
        rc.setThreshold(5000);

        vm.expectRevert(abi.encodeWithSelector(ReflexGuardRC.ThresholdOutOfBounds.selector, min - 1));
        rc.setThreshold(min - 1);
        vm.expectRevert(abi.encodeWithSelector(ReflexGuardRC.ThresholdOutOfBounds.selector, max + 1));
        rc.setThreshold(max + 1);

        rc.setThreshold(max);
        assertEq(rc.threshold(), max);
    }

    // ------------------------------------------------------------------ resume path

    function test_resumeLetsSwapsThroughAgain() public {
        vm.prank(PROXY);
        pool.haltPool(address(rc), 9_000, 1);
        vm.expectRevert(GuardedPool.PoolPaused.selector);
        pool.swap(true, 1e18, 0, address(this));

        vm.expectEmit(false, false, false, true, address(pool));
        emit GuardedPool.Resumed(block.timestamp);
        pool.resume();
        assertGt(pool.swap(true, 1e18, 0, address(this)), 0);
    }

    function test_haltIsIdempotent() public {
        vm.startPrank(PROXY);
        pool.haltPool(address(rc), 9_000, 1);
        pool.haltPool(address(rc), 9_500, 2);
        vm.stopPrank();
        assertTrue(pool.paused());
    }

    // ------------------------------------------------------------------ robustness of react()

    function _noEffect(IReactive.LogRecord memory log) internal {
        vm.recordLogs();
        _react(rc, log);
        assertEq(vm.getRecordedLogs().length, 0, "ignored log must not emit");
        assertEq(rc.flowState().priceEma, 0, "ignored log must not touch state");
        assertEq(_system().requestCount(), 0);
    }

    function test_malformedLogsAreIgnoredNotReverted() public {
        address p = address(pool);
        IReactive.LogRecord memory ok = _swapLog(p, address(this), true, 1e18, 1e18, 1e24, 1e24, clock, 1);

        IReactive.LogRecord memory bad = _swapLog(p, address(this), true, 1e18, 1e18, 1e24, 1e24, clock, 1);
        bad.data = hex"1234";
        _noEffect(bad);

        bad = _swapLog(p, address(this), true, 1e18, 1e18, 1e24, 1e24, clock, 1);
        bad.data = abi.encodePacked(bad.data, uint256(0));
        _noEffect(bad);

        bad = _swapLog(p, address(this), true, 1e18, 1e18, 1e24, 1e24, clock, 1);
        bad.data = "";
        _noEffect(bad);

        bad = _swapLog(p, address(this), true, 1e18, 1e18, 1e24, 1e24, clock, 1);
        bad.chainId = 1;
        _noEffect(bad);

        bad = _swapLog(address(0xF00D), address(this), true, 1e18, 1e18, 1e24, 1e24, clock, 1);
        _noEffect(bad);

        bad = _swapLog(p, address(this), true, 1e18, 1e18, 1e24, 1e24, clock, 1);
        bad.topic0 = uint256(keccak256("Mint(address,uint256)"));
        _noEffect(bad);

        bad = _swapLog(p, address(this), true, 1e18, 1e18, 1e24, 1e24, clock, 1);
        bad.topic0 = RESUMED_TOPIC; // right topic, wrong length (192 bytes)
        _noEffect(bad);

        // a bool word that is not 0 or 1 is read as true instead of reverting
        bad = ok;
        bad.data = abi.encode(uint256(2), 1e18, 1e18, 1e24, 1e24, clock);
        vm.recordLogs();
        _react(rc, bad);
        assertEq(vm.getRecordedLogs().length, 1, "scored");

        // zero reserves are skipped
        IReactive.LogRecord memory zero = _swapLog(p, address(this), true, 1e18, 1e18, 0, 1e24, clock, 2);
        vm.recordLogs();
        _react(rc, zero);
        assertEq(vm.getRecordedLogs().length, 0);
    }

    // ------------------------------------------------------------------ gas

    function test_reactGas() public {
        // first swap writes the two state slots from zero; later ones update them
        _react(rc, _swapLog(address(pool), address(this), true, 5e21, 5e21, 1.005e23, 0.995e23, clock, 1));
        uint256 firstGas = vm.lastFrameGas().gasTotalUsed;

        _react(rc, _swapLog(address(pool), address(this), false, 2e21, 2e21, 1.003e23, 0.997e23, clock + 3, 2));
        uint256 secondGas = vm.lastFrameGas().gasTotalUsed;

        _react(rc, _swapLog(address(pool), address(this), true, 1e21, 1e21, 1.004e23, 0.996e23, clock + 6, 3));
        uint256 thirdGas = vm.lastFrameGas().gasTotalUsed;

        console.log("react() gas, first swap (state slots 0 -> nonzero):", firstGas);
        console.log("react() gas, second swap:", secondGas);
        console.log("react() gas, third swap:", thirdGas);
        assertLt(firstGas, 900_000);
        assertLt(secondGas, 900_000);
        assertGt(firstGas, secondGas, "first swap pays for initialising the state slots");
    }
}
