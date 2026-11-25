// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import {Vm} from "forge-std/Test.sol";
import {console} from "forge-std/console.sol";
import {ReflexGuardRC} from "../src/reactive/ReflexGuardRC.sol";
import {IReactive} from "@reactive/src/interfaces/IReactive.sol";
import {Base} from "./Helpers.sol";

/// @notice Replays ml/vectors.json through react() and requires the on-chain score and features to equal
///         the Python integer reference exactly, for every swap of every sequence.
contract ParityTest is Base {
    address internal constant POOL = address(0xB001);

    struct Vectors {
        uint256[] start;
        uint256[] zeroForOne;
        uint256[] emitted;
        uint256[] score;
        uint256[] amountIn;
        uint256[] amountOut;
        uint256[] reserve0;
        uint256[] reserve1;
        uint256[] timestamp;
        uint256[] features;
    }

    function setUp() public {
        _etchSystem();
    }

    function _uints(string memory json, string memory key) internal pure returns (uint256[] memory out) {
        string[] memory raw = vm.parseJsonStringArray(json, key);
        out = new uint256[](raw.length);
        for (uint256 i; i < raw.length; ++i) {
            out[i] = vm.parseUint(raw[i]);
        }
    }

    function _load() internal view returns (Vectors memory v) {
        string memory json = vm.readFile("ml/vectors.json");
        v.start = vm.parseJsonUintArray(json, ".seqStart");
        v.zeroForOne = vm.parseJsonUintArray(json, ".zeroForOne");
        v.emitted = vm.parseJsonUintArray(json, ".expectedEmitted");
        v.score = vm.parseJsonUintArray(json, ".expectedScore");
        v.amountIn = _uints(json, ".amountIn");
        v.amountOut = _uints(json, ".amountOut");
        v.reserve0 = _uints(json, ".reserve0");
        v.reserve1 = _uints(json, ".reserve1");
        v.timestamp = _uints(json, ".timestamp");
        v.features = _uints(json, ".expectedFeatures");
    }

    function _logFor(Vectors memory v, uint256 i) internal pure returns (IReactive.LogRecord memory) {
        return _swapLog(
                POOL,
                address(0xA11CE),
                v.zeroForOne[i] == 1,
                v.amountIn[i],
                v.amountOut[i],
                v.reserve0[i],
                v.reserve1[i],
                v.timestamp[i],
                i + 1
            );
    }

    function _checkScored(Vm.Log[] memory logs, Vectors memory v, uint256 i, uint256 seq) internal pure {
        assertGe(logs.length, 1, "no Scored emitted");
        assertEq(logs[0].topics[0], ReflexGuardRC.Scored.selector);
        (uint256 score, uint256 feats, uint256 txHash, uint256 ts) =
            abi.decode(logs[0].data, (uint256, uint256, uint256, uint256));
        string memory where = string.concat("seq ", vm.toString(seq), " step ", vm.toString(i));
        assertEq(score, v.score[i], string.concat("score mismatch, ", where));
        assertEq(feats, v.features[i], string.concat("features mismatch, ", where));
        assertEq(txHash, i + 1);
        assertEq(ts, v.timestamp[i] > type(uint64).max ? type(uint64).max : v.timestamp[i]);
    }

    /// @dev Feed step `i` to the RC and check the emitted Scored event. Returns the gas used by react() and
    ///      whether the step was scored (skipped steps emit nothing) and requested a callback.
    function _step(ReflexGuardRC rc, Vectors memory v, uint256 i, uint256 seq)
        internal
        returns (uint256 gasUsed, bool scored, bool requested)
    {
        uint256 before = _system().requestCount();
        vm.recordLogs();
        _react(rc, _logFor(v, i));
        gasUsed = vm.lastFrameGas().gasTotalUsed; // the react() call only, not building the log
        Vm.Log[] memory logs = vm.getRecordedLogs();

        if (v.emitted[i] == 0) {
            assertEq(logs.length, 0, "skipped swap must not emit");
            return (gasUsed, false, false);
        }
        _checkScored(logs, v, i, seq);
        requested = _system().requestCount() != before;
        return (gasUsed, true, requested);
    }

    function test_parityAllVectors() public {
        Vectors memory v = _load();
        uint256 nSeq = v.start.length - 1;
        assertGe(nSeq, 40, "need at least 40 sequences");

        uint256 checked;
        uint256 plain;
        uint256 maxGas;
        uint256 sumGas;
        for (uint256 s; s < nSeq; ++s) {
            ReflexGuardRC rc = new ReflexGuardRC(POOL, CHAIN_ID, _threshold(), _weightWords());
            for (uint256 i = v.start[s]; i < v.start[s + 1]; ++i) {
                (uint256 g, bool scored, bool requested) = _step(rc, v, i, s);
                if (!scored) continue;
                checked++;
                if (requested) continue; // gas of the stub's own bookkeeping would be included
                plain++;
                sumGas += g;
                if (g > maxGas) maxGas = g;
            }
        }
        console.log("sequences checked:", nSeq);
        console.log("swaps checked (exact score + features):", checked);
        console.log("swaps without callback request:", plain);
        console.log("react() gas without callback, mean:", sumGas / plain);
        console.log("react() gas without callback, max:", maxGas);
        assertLt(maxGas, 900_000);
    }
}
