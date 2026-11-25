# ReflexGuard demo run

Run started 2026-11-25 10:09:23 UTC.

## Setup

- pool [`0xd98d4Dd5aD8420E49BB69b13105c35f786364965`](https://sepolia.basescan.org/address/0xd98d4Dd5aD8420E49BB69b13105c35f786364965) on Base Sepolia
- reactive contract [`0x313E31842F0Ef37D499a24D95b2F647948A8E1C5`](https://lasna.reactscan.net/address/0x313E31842F0Ef37D499a24D95b2F647948A8E1C5) on Lasna
- halt threshold: 8300 (score range 0-10000)

## 1. Benign flow including a whale swap

| step | direction | amount in (tokens) | tx | block time (UTC) |
|---|---|---|---|---|
| retail | token0->token1 | 200.0 | [`0x0f7ea027`](https://sepolia.basescan.org/tx/0x0f7ea0277de3cb097f6a1870b055b3252e638c7bc5f0a0d36c1763cf80f93a94) | 10:09:31 |
| retail | token1->token0 | 150.0 | [`0xb1a2d9f3`](https://sepolia.basescan.org/tx/0xb1a2d9f3039f14f09e1a080f6c58c0a3ac4d5d9c25f33993ea6a66be30eb67d9) | 10:09:33 |
| retail | token0->token1 | 300.0 | [`0x8ced0151`](https://sepolia.basescan.org/tx/0x8ced01515fa16fd17931291cc2be82ac2cb833a7d49c27cb84b3c6f2f14039e0) | 10:09:36 |
| retail | token1->token0 | 250.0 | [`0xfc64c66a`](https://sepolia.basescan.org/tx/0xfc64c66ae48cfec75c6dfc60188345b5b17689d1a6ac724d85a3f25ba7b54bbd) | 10:09:39 |
| retail | token0->token1 | 100.0 | [`0x11ca257c`](https://sepolia.basescan.org/tx/0x11ca257cb7f857014e6f417586e55b32b05b7795882f8aa0ce6cb04a6da92e3d) | 10:09:43 |
| whale | token0->token1 | 6,000.0 | [`0x1f0efdc0`](https://sepolia.basescan.org/tx/0x1f0efdc0ecb2391bddaae9a7e2de1d24eb346105b4bf295ee60a02e99b4fd89b) | 10:09:46 |
| retail | token1->token0 | 150.0 | [`0xa1eec2fe`](https://sepolia.basescan.org/tx/0xa1eec2fec93c9fa7a7e789e6b403ba0530e9c7ad5d3f47cae11efc660a3b4624) | 10:09:48 |
| retail | token0->token1 | 200.0 | [`0xc73b5c89`](https://sepolia.basescan.org/tx/0xc73b5c89ea69471e1bac69c1d38a2844bbc8ff46d7a598014d365f2a6b1f97f7) | 10:09:52 |
| retail | token1->token0 | 100.0 | [`0x1a4aaf00`](https://sepolia.basescan.org/tx/0x1a4aaf00cc7ba646d34c9665085aac8a558144ada3305737df322d4c0e58343d) | 10:09:54 |

No halt expected. Halted events so far: 0.

## 2. Pump and dump (family A)

| step | direction | amount in (tokens) | tx | block time (UTC) |
|---|---|---|---|---|
| pump | token1->token0 | 12,000.0 | [`0x111bb09f`](https://sepolia.basescan.org/tx/0x111bb09f2a2441ad7e2996d7a4faad2c0eeea718924ec2f462e895f847ce9e4a) | 10:09:56 |
| dump_1 | token0->token1 | 5,976.4 | [`0x8d0e787e`](https://sepolia.basescan.org/tx/0x8d0e787e60e3256d9c8bf1541a7a43f1e3cea68d6a3e0d516fdc5f501512bce0) | 10:09:59 |
| dump_2 | token0->token1 | 5,976.4 | reverted: `cast send --rpc-url https://sepolia.base.org --private-key <redacted> --json 0xd98d4Dd5aD8420E49BB69b13105c35f786364965 swap(bool,uint256,uint256,address) true ` | - |

### Pump and dump: latency

| stage | chain | tx | timestamp (UTC) | after swap (block time) | after swap (first seen by this script) |
|---|---|---|---|---|---|
| trigger swap `dump_1` | Base Sepolia | [`0x8d0e787e`](https://sepolia.basescan.org/tx/0x8d0e787e60e3256d9c8bf1541a7a43f1e3cea68d6a3e0d516fdc5f501512bce0) | 10:09:59 | 0 s | 0 s |
| Scored (score 9908) | Lasna | [`0x128c41d0`](https://lasna.reactscan.net/tx/0x128c41d09eb4d1452b66b54b3fdbdcecba7dba8512d5605ec75e96860ca4ff74) | 10:09:58 | -1 s | 4.5 s |
| Halted | Base Sepolia | [`0xef99c7da`](https://sepolia.basescan.org/tx/0xef99c7daf2f57345a8ad14bd2829901af900ce128d2b90bec7b97e624bab738f) | 10:10:00 | 1 s | 1.9 s |

Block times have 1 s resolution. The last column is wall-clock from the polling thread (0.5 s poll, RPC latency included), measured from the moment `cast` returned the swap receipt.

### Swap attempted after the halt

The swap reverted as expected: `cast send --rpc-url https://sepolia.base.org --private-key <redacted> --json 0xd98d4Dd5aD8420E49BB69b13105c35f786364965 swap(bool,uint256,uint256,address) true 1000000000000000000 0 0x3F96C6fb40776B53`

## 3. Slow drip and reversal (family C)

### Owner resumes the pool

[`0xfd185c6f`](https://sepolia.basescan.org/tx/0xfd185c6ff0e9cfb2c500564dbfbf13aa16f4e32b417e1fe650cb8f335b3e2957): `Resumed` emitted.
The Reactive Contract re-armed itself from the `Resumed` log.

| step | direction | amount in (tokens) | tx | block time (UTC) |
|---|---|---|---|---|
| drip | token0->token1 | 3,000.0 | [`0xc229053e`](https://sepolia.basescan.org/tx/0xc229053ed384922fa8304be7ee2b2eb926e24540fd9fa3ae81e260c31575a3a2) | 10:10:11 |
| drip | token0->token1 | 3,000.0 | [`0xf00759a5`](https://sepolia.basescan.org/tx/0xf00759a5f2515174d27177de4f00091bd6e19f95ce19bc4345cc1a2d9ba56886) | 10:10:14 |
| drip | token0->token1 | 3,000.0 | [`0x6b5d4999`](https://sepolia.basescan.org/tx/0x6b5d499904c6ca6d8c7375a55628fe43f641948c8109ba10cfa948c622bcd31e) | 10:10:17 |
| drip | token0->token1 | 3,000.0 | [`0x8e127f48`](https://sepolia.basescan.org/tx/0x8e127f486e21227075ff1959a5641ce3eda7b0fa8b139257c6b760471f0f7d30) | 10:10:20 |
| drip | token0->token1 | 3,000.0 | [`0xcd6403ee`](https://sepolia.basescan.org/tx/0xcd6403eeb55db22cc5f2223459a139392bac28453f117963f5db60fd019a8fdf) | 10:10:23 |
| drip | token0->token1 | 3,000.0 | [`0xb522c221`](https://sepolia.basescan.org/tx/0xb522c221e4f1e7d3a43feb9c94ef2ab959f8174e3dedefb72eb22203e98db5b7) | 10:10:26 |
| drip | token0->token1 | 3,000.0 | [`0x15e9382e`](https://sepolia.basescan.org/tx/0x15e9382ed0b9bb326105eb93483a7cd3bdc57896259db7504d37900e35173fd9) | 10:10:29 |
| reverse | token1->token0 | 15,539.3 | [`0x9771765f`](https://sepolia.basescan.org/tx/0x9771765f67f98d87f537c7002ad3edb839054cf28dc683235b0fdc370a1e5094) | 10:10:32 |

### Slow drip: latency

| stage | chain | tx | timestamp (UTC) | after swap (block time) | after swap (first seen by this script) |
|---|---|---|---|---|---|
| trigger swap `reverse` | Base Sepolia | [`0x9771765f`](https://sepolia.basescan.org/tx/0x9771765f67f98d87f537c7002ad3edb839054cf28dc683235b0fdc370a1e5094) | 10:10:32 | 0 s | 0 s |
| Scored (score 9991) | Lasna | [`0xd75f9235`](https://lasna.reactscan.net/tx/0xd75f9235a89a693503af456e3e3a0631afeb28054c8726428e391a17628d70cd) | 10:10:31 | -1 s | 1.4 s |
| Halted | Base Sepolia | [`0x6c7f2e85`](https://sepolia.basescan.org/tx/0x6c7f2e85fa5004103fc244ccb2a36472c6fe59cc84aca0893485ba45b5b76a5d) | 10:10:33 | 1 s | 2.0 s |

Block times have 1 s resolution. The last column is wall-clock from the polling thread (0.5 s poll, RPC latency included), measured from the moment `cast` returned the swap receipt.

### Swap attempted after the halt

The swap reverted as expected: `cast send --rpc-url https://sepolia.base.org --private-key <redacted> --json 0xd98d4Dd5aD8420E49BB69b13105c35f786364965 swap(bool,uint256,uint256,address) true 1000000000000000000 0 0x3F96C6fb40776B53`

## On-chain score vs Python integer reference

| swap | tx | on-chain score | python score | match |
|---|---|---|---|---|
| retail | `0x0f7ea027` | 3 | 3 | yes |
| retail | `0xb1a2d9f3` | 3 | 3 | yes |
| retail | `0x8ced0151` | 3 | 3 | yes |
| retail | `0xfc64c66a` | 3 | 3 | yes |
| retail | `0x11ca257c` | 3 | 3 | yes |
| whale | `0x1f0efdc0` | 50 | 50 | yes |
| retail | `0xa1eec2fe` | 112 | 112 | yes |
| retail | `0xc73b5c89` | 3 | 3 | yes |
| retail | `0x1a4aaf00` | 55 | 55 | yes |
| pump | `0x111bb09f` | 3702 | 3702 | yes |
| dump_1 | `0x8d0e787e` | 9908 | 9908 | yes |
| drip | `0xc229053e` | 50 | 50 | yes |
| drip | `0xf00759a5` | 13 | 13 | yes |
| drip | `0x6b5d4999` | 3 | 3 | yes |
| drip | `0x8e127f48` | 3 | 3 | yes |
| drip | `0xcd6403ee` | 3 | 3 | yes |
| drip | `0xb522c221` | 3 | 3 | yes |
| drip | `0x15e9382e` | 3 | 3 | yes |
| reverse | `0x9771765f` | 9991 | 9991 | yes |

Mismatches: 0.

