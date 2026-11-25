// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import {AbstractCallback} from "@reactive/src/base/AbstractCallback.sol";
import {IPayable} from "@reactive/src/interfaces/IPayable.sol";

interface IERC20Min {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @title GuardedPool
/// @notice Constant-product AMM (0.3% fee) that can be paused by its Reactive Contract.
/// @dev The pool is also the callback target: `haltPool` is reachable only through the Reactive callback
///      proxy and only when the proxy injects the expected reactive contract address as first argument.
///      There is no LP accounting; liquidity is seeded by the owner and cannot be withdrawn. Demo contract.
contract GuardedPool is AbstractCallback {
    /// @notice Emitted on every swap with the POST-swap reserves.
    event Swap(
        address indexed sender,
        bool zeroForOne,
        uint256 amountIn,
        uint256 amountOut,
        uint256 reserve0,
        uint256 reserve1,
        uint256 timestamp
    );
    /// @notice Emitted when the Reactive Contract pauses the pool.
    event Halted(uint256 score, uint256 triggerTxHash, uint256 timestamp);
    /// @notice Emitted when the owner unpauses the pool.
    event Resumed(uint256 timestamp);

    error PoolPaused();
    error NotOwner();
    error ZeroAmount();
    error Slippage(uint256 amountOut, uint256 minOut);

    uint256 public constant FEE_NUMERATOR = 997;
    uint256 public constant FEE_DENOMINATOR = 1000;

    IERC20Min public immutable token0;
    IERC20Min public immutable token1;
    address public immutable owner;

    uint256 public reserve0;
    uint256 public reserve1;
    bool public paused;

    /// @param token0_ First pool token.
    /// @param token1_ Second pool token.
    /// @param callbackProxy_ Reactive callback proxy on this chain.
    /// @param reactive_ The only Reactive Contract allowed to halt this pool.
    /// @param owner_ Account allowed to seed liquidity and resume the pool.
    constructor(address token0_, address token1_, IPayable callbackProxy_, address reactive_, address owner_)
        payable
        AbstractCallback(callbackProxy_, reactive_)
    {
        token0 = IERC20Min(token0_);
        token1 = IERC20Min(token1_);
        owner = owner_;
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @notice Quote the output of a swap against the current reserves.
    function getAmountOut(bool zeroForOne, uint256 amountIn) public view returns (uint256) {
        (uint256 rIn, uint256 rOut) = zeroForOne ? (reserve0, reserve1) : (reserve1, reserve0);
        uint256 inWithFee = amountIn * FEE_NUMERATOR;
        return inWithFee * rOut / (rIn * FEE_DENOMINATOR + inWithFee);
    }

    /// @notice Swap an exact input amount. Reverts while the pool is paused.
    /// @param zeroForOne True to sell token0 for token1.
    /// @param amountIn Exact input amount, pulled from the caller (needs allowance).
    /// @param minOut Minimum acceptable output.
    /// @param to Recipient of the output tokens.
    /// @return amountOut Output amount sent to `to`.
    function swap(bool zeroForOne, uint256 amountIn, uint256 minOut, address to) external returns (uint256 amountOut) {
        if (paused) revert PoolPaused();
        if (amountIn == 0) revert ZeroAmount();
        amountOut = getAmountOut(zeroForOne, amountIn);
        if (amountOut < minOut || amountOut == 0) revert Slippage(amountOut, minOut);

        (IERC20Min tIn, IERC20Min tOut) = zeroForOne ? (token0, token1) : (token1, token0);
        if (zeroForOne) {
            reserve0 += amountIn;
            reserve1 -= amountOut;
        } else {
            reserve1 += amountIn;
            reserve0 -= amountOut;
        }
        _safe(address(tIn), abi.encodeCall(IERC20Min.transferFrom, (msg.sender, address(this), amountIn)));
        _safe(address(tOut), abi.encodeCall(IERC20Min.transfer, (to, amountOut)));

        emit Swap(msg.sender, zeroForOne, amountIn, amountOut, reserve0, reserve1, block.timestamp);
    }

    /// @notice Seed liquidity. Owner only; there is no way to withdraw it.
    function addLiquidity(uint256 amount0, uint256 amount1) external onlyOwner {
        reserve0 += amount0;
        reserve1 += amount1;
        _safe(address(token0), abi.encodeCall(IERC20Min.transferFrom, (msg.sender, address(this), amount0)));
        _safe(address(token1), abi.encodeCall(IERC20Min.transferFrom, (msg.sender, address(this), amount1)));
    }

    /// @notice Callback entry point: pause the pool.
    /// @param rc Reactive Contract address, overwritten by the callback proxy; must equal the configured sender.
    /// @param score Risk score (0..10000) that triggered the halt.
    /// @param triggerTxHash Hash of the swap transaction that triggered the halt.
    function haltPool(address rc, uint256 score, uint256 triggerTxHash)
        external
        onlyServiceProvider
        onlyCallbackSender(rc)
    {
        if (paused) return;
        paused = true;
        emit Halted(score, triggerTxHash, block.timestamp);
    }

    /// @notice Unpause the pool. Owner only.
    function resume() external onlyOwner {
        paused = false;
        emit Resumed(block.timestamp);
    }

    /// @notice Pay any outstanding callback debt to the callback proxy from this contract's balance.
    function coverDebt() external {
        _coverDebt();
    }

    function _safe(address token, bytes memory data) internal {
        (bool ok, bytes memory ret) = token.call(data);
        if (!ok || (ret.length != 0 && !abi.decode(ret, (bool)))) revert TransferFailed();
    }
}
