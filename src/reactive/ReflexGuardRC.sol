// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import {AbstractReactive} from "@reactive/src/base/AbstractReactive.sol";
import {ISystemContract} from "@reactive/src/interfaces/ISystemContract.sol";
import {ReflexTables} from "./ReflexTables.sol";

/// @title ReflexGuardRC
/// @notice Reactive Contract that scores every swap of one pool with a small fixed-point MLP and asks the
///         pool to pause (cross-chain callback) when the score reaches a threshold.
/// @dev All arithmetic is integer and mirrors ml/features.py operation for operation (see docs/SPEC.md).
///      react() never reverts on a malformed or foreign log: it returns early instead.
contract ReflexGuardRC is AbstractReactive {
    /// @notice Emitted for every scored swap.
    /// @param features Six int16 Q10 features, feature 0 in the lowest 16 bits.
    event Scored(address indexed pool, uint256 score, uint256 features, uint256 txHash, uint256 timestamp);
    /// @notice Emitted when a halt callback has been requested.
    event HaltRequested(address indexed pool, uint256 score, uint256 txHash);
    /// @notice Emitted when the system contract rejects a callback request.
    event HaltRequestFailed(address indexed pool, uint256 score, uint256 txHash);
    /// @notice Emitted when the pool reports `Resumed` and the guard re-arms.
    event Rearmed(address indexed pool, uint256 timestamp);
    /// @notice Emitted when the owner changes the threshold.
    event ThresholdChanged(uint256 threshold);

    error NotOwner();
    error ThresholdOutOfBounds(uint256 threshold);

    /// @dev Rolling state, packed into two storage slots. priceEma == 0 means "no swap seen yet" (a real
    ///      price is clipped to >= 1). The 64-bit fields cannot overflow: each swap adds at most 2^16.
    struct Flow {
        int128 priceEma; // Q32 price (token1 per token0), half-life 128 s
        int128 reserveEma; // reserve0, half-life 1024 s
        int64 flow; // decayed signed flow, Q16 pool fraction, positive = token0 in
        int64 volume; // decayed absolute volume, Q16 pool fraction
        int64 rate; // decayed swap count, Q16
        uint64 timestamp; // timestamp of the last swap seen
    }

    /// @dev Working copy of `Flow` with full-width integers.
    struct Work {
        int256 priceEma;
        int256 reserveEma;
        int256 flow;
        int256 volume;
        int256 rate;
        uint256 timestamp;
    }

    /// @dev keccak256("Swap(address,bool,uint256,uint256,uint256,uint256,uint256)")
    uint256 internal constant SWAP_TOPIC = uint256(keccak256("Swap(address,bool,uint256,uint256,uint256,uint256,uint256)"));
    /// @dev keccak256("Resumed(uint256)")
    uint256 internal constant RESUMED_TOPIC = uint256(keccak256("Resumed(uint256)"));

    uint256 internal constant SWAP_DATA_LENGTH = 192;
    int256 internal constant Q16 = 1 << 16;
    uint256 internal constant MAX_RESERVE = 1 << 96;
    int256 internal constant MAX_PRICE = (1 << 127) - 1;
    uint256 internal constant MAX_TIMESTAMP = type(uint64).max;
    int256 internal constant FEATURE_MAX = 2048;
    int256 internal constant HIDDEN_MAX = 16383;
    int256 internal constant LOGIT_MAX = 8192;
    uint256 internal constant N_IN = 6;
    uint256 internal constant N_HIDDEN = 8;

    /// @notice Gas limit requested for the destination-chain callback.
    uint64 public constant CALLBACK_GAS = 500_000;
    /// @notice Lowest threshold the owner may set.
    uint256 public constant MIN_THRESHOLD = 3000;
    /// @notice Highest threshold the owner may set.
    uint256 public constant MAX_THRESHOLD = 9990;
    /// @notice Seconds after a halt request before a new one is sent if the pool keeps trading.
    uint256 public constant RETRY_AFTER = 60;

    address public immutable owner;
    address public immutable pool;
    uint256 public immutable originChainId;
    // 65 int16 weights, 16 per word: W1 (8x6, row major), b1 (8), W2 (8), b2 (1).
    uint256 internal immutable W0;
    uint256 internal immutable W1;
    uint256 internal immutable W2;
    uint256 internal immutable W3;
    uint256 internal immutable W4;

    /// @notice Score (0..10000) at or above which a halt is requested.
    uint256 public threshold;
    /// @notice Event timestamp of the last halt request, 0 when armed.
    uint64 public haltRequestedAt;

    Flow internal _flow;

    /// @param pool_ Pool on the origin chain whose swaps are scored.
    /// @param originChainId_ Chain id of the pool; also the callback destination.
    /// @param threshold_ Initial halt threshold.
    /// @param weights_ Packed MLP weights, see ml/features.py `pack_weights`.
    constructor(address pool_, uint256 originChainId_, uint256 threshold_, uint256[5] memory weights_) payable {
        _checkThreshold(threshold_);
        owner = msg.sender;
        pool = pool_;
        originChainId = originChainId_;
        threshold = threshold_;
        W0 = weights_[0];
        W1 = weights_[1];
        W2 = weights_[2];
        W3 = weights_[3];
        W4 = weights_[4];

        SYSTEM.subscribe(originChainId_, pool_, SWAP_TOPIC, REACTIVE_IGNORE, REACTIVE_IGNORE, REACTIVE_IGNORE);
        SYSTEM.subscribe(originChainId_, pool_, RESUMED_TOPIC, REACTIVE_IGNORE, REACTIVE_IGNORE, REACTIVE_IGNORE);
    }

    /// @notice Change the halt threshold within [MIN_THRESHOLD, MAX_THRESHOLD]. Owner only.
    function setThreshold(uint256 threshold_) external {
        if (msg.sender != owner) revert NotOwner();
        _checkThreshold(threshold_);
        threshold = threshold_;
        emit ThresholdChanged(threshold_);
    }

    /// @notice Pay outstanding debt to the system contract from this contract's balance.
    function coverDebt() external {
        _coverDebt();
    }

    /// @notice Current rolling state, for monitoring.
    function flowState() external view returns (Flow memory) {
        return _flow;
    }

    /// @notice Entry point called by the system contract for each subscribed log.
    /// @dev Unknown logs, wrong emitters and malformed payloads are ignored.
    function react(LogRecord calldata log_) external onlySystem {
        if (log_.chainId != originChainId || log_.contractAddress != pool) return;
        if (log_.topic0 == SWAP_TOPIC) {
            _onSwap(log_);
        } else if (log_.topic0 == RESUMED_TOPIC && log_.data.length == 32) {
            delete _flow;
            haltRequestedAt = 0;
            emit Rearmed(pool, uint256(bytes32(log_.data[0:32])));
        }
    }

    function _onSwap(LogRecord calldata log_) internal {
        bytes calldata d = log_.data;
        if (d.length != SWAP_DATA_LENGTH) return;

        uint256 ts = uint256(bytes32(d[160:192]));
        if (ts > MAX_TIMESTAMP) ts = MAX_TIMESTAMP;
        bool zeroForOne = uint256(bytes32(d[0:32])) != 0;
        (bool ok, int256 rel, int256 price, int256 reserve0) = _normalise(
            zeroForOne, uint256(bytes32(d[32:64])), uint256(bytes32(d[96:128])), uint256(bytes32(d[128:160]))
        );
        if (!ok) return;

        int256[6] memory f = _update(zeroForOne ? int256(1) : int256(-1), rel, price, reserve0, ts);
        uint256 score = _mlp(f);
        uint256 packed;
        for (uint256 i; i < N_IN; ++i) {
            packed |= uint256(uint16(int16(f[i]))) << (16 * i);
        }
        emit Scored(pool, score, packed, log_.txHash, ts);

        if (score >= threshold) _requestHalt(score, log_.txHash, ts);
    }

    /// @dev Clip the raw event values and derive the swap size as a Q16 fraction of the input-side reserve
    ///      and the Q32 price. Returns ok = false when a reserve is zero.
    function _normalise(bool zeroForOne, uint256 amountIn, uint256 reserve0, uint256 reserve1)
        internal
        pure
        returns (bool ok, int256 rel, int256 price, int256 reserve0Signed)
    {
        if (reserve0 == 0 || reserve1 == 0) return (false, 0, 0, 0);
        if (reserve0 > MAX_RESERVE) reserve0 = MAX_RESERVE;
        if (reserve1 > MAX_RESERVE) reserve1 = MAX_RESERVE;
        uint256 reserveIn = zeroForOne ? reserve0 : reserve1;
        if (amountIn > reserveIn) amountIn = reserveIn;
        rel = int256(amountIn * uint256(Q16) / reserveIn);
        price = _clip(int256((reserve1 << 32) / reserve0), 1, MAX_PRICE);
        return (true, rel, price, int256(reserve0));
    }

    /// @dev Advance the rolling state with one swap and return the six Q10 features.
    ///      An uninitialised state starts with both EMAs at the current values and zero flow, so the
    ///      decay step is a no-op for it.
    function _update(int256 sign, int256 rel, int256 price, int256 reserve0, uint256 ts)
        internal
        returns (int256[6] memory f)
    {
        Flow memory stored = _flow;
        Work memory s = Work(stored.priceEma, stored.reserveEma, stored.flow, stored.volume, stored.rate, stored.timestamp);
        bool seen = s.priceEma != 0;
        if (!seen) {
            s.priceEma = price;
            s.reserveEma = reserve0;
        }
        uint256 dt = seen && ts > s.timestamp ? ts - s.timestamp : 0;
        int256 dFlow = _decay(dt, 5);
        s.flow = (s.flow * dFlow) >> 16;
        s.volume = (s.volume * dFlow) >> 16;
        s.rate = (s.rate * dFlow) >> 16;

        f[0] = _min(rel >> 3, FEATURE_MAX);
        f[1] = _clip((-sign * s.flow) >> 3, -FEATURE_MAX, FEATURE_MAX);
        f[2] = _clip((reserve0 - s.reserveEma) * 8192 / _max(s.reserveEma, 1), -FEATURE_MAX, FEATURE_MAX);

        s.flow += sign * rel;
        s.volume += rel;
        s.rate += Q16;

        f[3] = _min(s.rate >> 10, FEATURE_MAX);
        f[4] = _min(s.volume >> 4, FEATURE_MAX);
        f[5] = _clip((price - s.priceEma) * 8192 / _max(s.priceEma, 1), -FEATURE_MAX, FEATURE_MAX);

        s.priceEma += ((price - s.priceEma) * (Q16 - _decay(dt, 7))) >> 16;
        s.reserveEma += ((reserve0 - s.reserveEma) * (Q16 - _decay(dt, 10))) >> 16;
        _flow = Flow(int128(s.priceEma), int128(s.reserveEma), int64(s.flow), int64(s.volume), int64(s.rate), uint64(ts));
    }

    /// @dev 6 -> 8 (ReLU) -> 1. Weights are Q8, activations Q10, accumulators int256.
    function _mlp(int256[6] memory f) internal view returns (uint256) {
        uint256[5] memory words = [W0, W1, W2, W3, W4];
        int256 out;
        for (uint256 j; j < N_HIDDEN; ++j) {
            int256 acc;
            for (uint256 i; i < N_IN; ++i) {
                acc += _weight(words, j * N_IN + i) * f[i];
            }
            int256 h = _clip((acc >> 8) + _weight(words, 48 + j), 0, HIDDEN_MAX);
            out += _weight(words, 56 + j) * h;
        }
        return _sigmoid(_clip((out >> 8) + _weight(words, 64), -LOGIT_MAX, LOGIT_MAX));
    }

    /// @dev Piecewise-linear sigmoid over 33 table points, 0.5 apart on [-8, 8]; logit is Q10.
    function _sigmoid(int256 logit) internal pure returns (uint256) {
        bytes memory t = ReflexTables.SIGMOID;
        uint256 z = uint256(logit + LOGIT_MAX);
        uint256 i = z >> 9;
        uint256 lo = _u16(t, i);
        if (i >= 32) return lo;
        return lo + (((_u16(t, i + 1) - lo) * (z & 511)) >> 9);
    }

    /// @dev Q16 factor 2^(-dt / 2^k): whole half-lives by shift, remainder from a 32-entry table.
    function _decay(uint256 dt, uint256 k) internal pure returns (int256) {
        bytes memory t = ReflexTables.DECAY;
        uint256 frac = (dt & ((1 << k) - 1)) >> (k - 5);
        uint256 v;
        assembly ("memory-safe") {
            v := shr(224, mload(add(add(t, 32), shl(2, frac))))
        }
        return int256(v >> (dt >> k));
    }

    function _requestHalt(uint256 score, uint256 txHash, uint256 ts) internal {
        uint256 at = haltRequestedAt;
        if (at != 0 && ts < at + RETRY_AFTER) return;
        try SYSTEM.requestCallbackV_1_0(
            ISystemContract.CallbackConfiguration_V_1_0({
                chainId: originChainId,
                recipient: pool,
                gasLimit: CALLBACK_GAS,
                payload: abi.encodeWithSignature("haltPool(address,uint256,uint256)", address(0), score, txHash)
            })
        ) {
            haltRequestedAt = uint64(ts == 0 ? 1 : ts);
            emit HaltRequested(pool, score, txHash);
        } catch {
            emit HaltRequestFailed(pool, score, txHash);
        }
    }

    function _checkThreshold(uint256 threshold_) internal pure {
        if (threshold_ < MIN_THRESHOLD || threshold_ > MAX_THRESHOLD) revert ThresholdOutOfBounds(threshold_);
    }

    function _weight(uint256[5] memory words, uint256 n) internal pure returns (int256) {
        return int256(int16(uint16(words[n >> 4] >> ((n & 15) * 16))));
    }

    function _u16(bytes memory t, uint256 i) internal pure returns (uint256 v) {
        assembly ("memory-safe") {
            v := shr(240, mload(add(add(t, 32), shl(1, i))))
        }
    }

    function _clip(int256 x, int256 lo, int256 hi) internal pure returns (int256) {
        return x < lo ? lo : x > hi ? hi : x;
    }

    function _min(int256 a, int256 b) internal pure returns (int256) {
        return a < b ? a : b;
    }

    function _max(int256 a, int256 b) internal pure returns (int256) {
        return a > b ? a : b;
    }
}
