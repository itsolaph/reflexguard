// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import {Script, console} from "forge-std/Script.sol";
import {IPayable} from "@reactive/src/interfaces/IPayable.sol";
import {GuardedPool} from "../src/origin/GuardedPool.sol";
import {MockToken} from "../src/origin/MockToken.sol";

/// @notice Step 3 (Base Sepolia): deploy the pool with the Reactive Contract as its only authorised callback
///         sender, fund it for callback fees and seed liquidity. Aborts if the pool address differs from the
///         one the Reactive Contract was deployed with.
///
///         Environment: PRIVATE_KEY, TOKEN0, TOKEN1, RC, POOL (expected address), POOL_FUND (wei kept in the
///         pool to pay callback fees, default 0.02 ether), CALLBACK_PROXY (default: Base Sepolia proxy).
contract DeployPool is Script {
    address internal constant BASE_SEPOLIA_PROXY = 0xa6eA49Ed671B8a4dfCDd34E36b7a75Ac79B8A5a6;

    function run() external {
        uint256 key = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(key);
        address expected = vm.envAddress("POOL");
        address proxy = vm.envOr("CALLBACK_PROXY", BASE_SEPOLIA_PROXY);
        uint256 fund = vm.envOr("POOL_FUND", uint256(0.02 ether));
        uint256 liquidity = vm.parseUint(vm.parseJsonString(vm.readFile("ml/demo_plan.json"), ".liquidity"));
        MockToken token0 = MockToken(vm.envAddress("TOKEN0"));
        MockToken token1 = MockToken(vm.envAddress("TOKEN1"));

        vm.startBroadcast(key);
        GuardedPool pool = new GuardedPool{value: fund}(
            address(token0), address(token1), IPayable(payable(proxy)), vm.envAddress("RC"), deployer
        );
        require(address(pool) == expected, "pool address differs from the one the RC was deployed with");
        token0.approve(address(pool), liquidity);
        token1.approve(address(pool), liquidity);
        pool.addLiquidity(liquidity, liquidity);
        vm.stopBroadcast();

        console.log(string.concat("POOL=", vm.toString(address(pool))));
    }
}
