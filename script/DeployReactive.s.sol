// SPDX-License-Identifier: MIT
pragma solidity ^0.8.29;

import {Script, console} from "forge-std/Script.sol";
import {ReflexGuardRC} from "../src/reactive/ReflexGuardRC.sol";

/// @notice Step 2 (Reactive Lasna): deploy the Reactive Contract with weights and threshold from
///         ml/weights.json. The pool does not exist yet; its address is the CREATE address the deployer's
///         next Base Sepolia transaction will produce (computed by scripts/deploy.sh).
///
///         Environment: PRIVATE_KEY, POOL (predicted pool address), RC_FUND (wei sent to the RC, default
///         0.1 ether), ORIGIN_CHAIN_ID (default 84532).
contract DeployReactive is Script {
    function run() external {
        uint256 key = vm.envUint("PRIVATE_KEY");
        address pool = vm.envAddress("POOL");
        uint256 fund = vm.envOr("RC_FUND", uint256(0.1 ether));
        uint256 originChainId = vm.envOr("ORIGIN_CHAIN_ID", uint256(84532));

        string memory json = vm.readFile("ml/weights.json");
        bytes32[] memory raw = vm.parseJsonBytes32Array(json, ".packed");
        uint256[5] memory words;
        for (uint256 i; i < 5; ++i) {
            words[i] = uint256(raw[i]);
        }
        uint256 threshold = vm.parseJsonUint(json, ".threshold");

        vm.startBroadcast(key);
        ReflexGuardRC rc = new ReflexGuardRC{value: fund}(pool, originChainId, threshold, words);
        vm.stopBroadcast();

        console.log(string.concat("RC=", vm.toString(address(rc))));
    }
}
