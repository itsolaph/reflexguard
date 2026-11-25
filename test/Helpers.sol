// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import {Test, Vm} from "forge-std/Test.sol";
import {ISystemContract} from "@reactive/src/interfaces/ISystemContract.sol";
import {IReactive} from "@reactive/src/interfaces/IReactive.sol";
import {ReflexGuardRC} from "../src/reactive/ReflexGuardRC.sol";
import {GuardedPool} from "../src/origin/GuardedPool.sol";
import {MockToken} from "../src/origin/MockToken.sol";

/// @dev Stand-in for the Reactive system contract: accepts subscriptions and records callback requests.
contract MockSystem {
    struct Request {
        uint256 chainId;
        address recipient;
        uint64 gasLimit;
        bytes payload;
    }

    uint256 public subscriptions;
    Request[] internal _requests;

    function subscribe(uint256, address, uint256, uint256, uint256, uint256) external {
        subscriptions++;
    }

    function requestCallbackV_1_0(ISystemContract.CallbackConfiguration_V_1_0 memory config) external {
        _requests.push(Request(config.chainId, config.recipient, config.gasLimit, config.payload));
    }

    function debt(address) external pure returns (uint256) {
        return 0;
    }

    function requestCount() external view returns (uint256) {
        return _requests.length;
    }

    function request(uint256 i) external view returns (Request memory) {
        return _requests[i];
    }

    receive() external payable {}
}

/// @dev Shared setup: system stub, weights loaded from ml/weights.json, log relay from pool to RC.
abstract contract Base is Test {
    address internal constant SYSTEM = 0x8888888888888888888888888888888888888888;
    address internal constant PROXY = 0xa6eA49Ed671B8a4dfCDd34E36b7a75Ac79B8A5a6;
    uint256 internal constant CHAIN_ID = 84532;
    uint256 internal constant SWAP_TOPIC = uint256(keccak256("Swap(address,bool,uint256,uint256,uint256,uint256,uint256)"));
    uint256 internal constant RESUMED_TOPIC = uint256(keccak256("Resumed(uint256)"));

    function _etchSystem() internal {
        vm.etch(SYSTEM, address(new MockSystem()).code);
    }

    function _system() internal pure returns (MockSystem) {
        return MockSystem(payable(SYSTEM));
    }

    function _weightWords() internal view returns (uint256[5] memory words) {
        string memory json = vm.readFile("ml/weights.json");
        bytes32[] memory raw = vm.parseJsonBytes32Array(json, ".packed");
        for (uint256 i; i < 5; ++i) {
            words[i] = uint256(raw[i]);
        }
    }

    function _threshold() internal view returns (uint256) {
        return vm.parseJsonUint(vm.readFile("ml/weights.json"), ".threshold");
    }

    function _swapLog(
        address pool,
        address sender,
        bool zeroForOne,
        uint256 amountIn,
        uint256 amountOut,
        uint256 reserve0,
        uint256 reserve1,
        uint256 timestamp,
        uint256 txHash
    ) internal pure returns (IReactive.LogRecord memory) {
        return IReactive.LogRecord({
            chainId: CHAIN_ID,
            contractAddress: pool,
            topic0: SWAP_TOPIC,
            topic1: uint256(uint160(sender)),
            topic2: 0,
            topic3: 0,
            data: abi.encode(zeroForOne, amountIn, amountOut, reserve0, reserve1, timestamp),
            blockNumber: 1,
            opCode: 0,
            blockHash: 0,
            txHash: txHash,
            logIndex: 0
        });
    }

    function _react(ReflexGuardRC rc, IReactive.LogRecord memory log) internal {
        vm.prank(SYSTEM);
        rc.react(log);
    }

    /// @dev Convert the pool's recorded logs into LogRecords and feed them to the RC, as the network would.
    function _relay(ReflexGuardRC rc, address pool, Vm.Log[] memory logs, uint256 txHash) internal {
        for (uint256 i; i < logs.length; ++i) {
            if (logs[i].emitter != pool) continue;
            bytes32 t0 = logs[i].topics[0];
            if (uint256(t0) != SWAP_TOPIC && uint256(t0) != RESUMED_TOPIC) continue;
            _react(
                rc,
                IReactive.LogRecord({
                    chainId: CHAIN_ID,
                    contractAddress: pool,
                    topic0: uint256(t0),
                    topic1: logs[i].topics.length > 1 ? uint256(logs[i].topics[1]) : 0,
                    topic2: 0,
                    topic3: 0,
                    data: logs[i].data,
                    blockNumber: block.number,
                    opCode: 0,
                    blockHash: 0,
                    txHash: txHash,
                    logIndex: i
                })
            );
        }
    }
}
