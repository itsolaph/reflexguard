// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import {Script, console} from "forge-std/Script.sol";
import {MockToken} from "../src/origin/MockToken.sol";

/// @notice Step 1 (Base Sepolia): deploy the two test tokens and mint a working balance to the deployer.
///         Reads PRIVATE_KEY from the environment. Prints DEPLOYER, TOKEN0 and TOKEN1 as KEY=value lines.
contract DeployTokens is Script {
    uint256 internal constant MINT = 10_000_000 ether;

    function run() external {
        uint256 key = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(key);
        vm.startBroadcast(key);
        MockToken token0 = new MockToken("ReflexGuard Test Token 0", "RGT0");
        MockToken token1 = new MockToken("ReflexGuard Test Token 1", "RGT1");
        token0.mint(deployer, MINT);
        token1.mint(deployer, MINT);
        vm.stopBroadcast();

        console.log(string.concat("DEPLOYER=", vm.toString(deployer)));
        console.log(string.concat("TOKEN0=", vm.toString(address(token0))));
        console.log(string.concat("TOKEN1=", vm.toString(address(token1))));
    }
}
