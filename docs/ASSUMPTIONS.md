# Assumptions and unverified items

Everything below was **not** checked against a live Reactive or Base Sepolia deployment. Nothing in this
repository has been deployed or broadcast; the tests run against a stub of the system contract.

## Reactive Network behaviour

1. **`react()` shares storage with the owner's transactions.** The Reactive docs page on reactive contracts
   describes `react()` as an ordinary transaction and shows an `onlyOwner` setter changing state that
   `react()` reads. It does not mention a separate ReactVM state copy for the omni library. The RC therefore has an
   owner-settable threshold (bounded to 3000..9990). If the real network keeps a split state, the constructor
   value is the one `react()` sees and `setThreshold` has no effect on it. Weights are constructor-only
   immutables either way.
2. **Subscribing in the constructor works.** `reactive-lib-omni`'s `AbstractReactive` does not subscribe on its
   own and has no ReactVM detection; the RC calls `SYSTEM.subscribe` twice in its constructor, as the docs
   example does. Not tried on Lasna.
3. **`LogRecord.txHash` is the origin transaction hash.** The RC reports it in `Scored` and passes it to the
   pool as `triggerTxHash`; `scripts/demo.py` matches on it. If the network fills it differently, the latency
   tables and the on-chain/Python comparison in the demo will not line up (scores themselves are unaffected).
4. **`requestCallbackV_1_0` can be wrapped in `try/catch`.** The RC does this so that a rejected request cannot
   revert `react()`. The test stub never rejects, so that branch is untested.
5. **The callback proxy replaces the first argument with the RC address** and calls the pool with the gas limit
   requested (200,000). This follows the library comments; the tests emulate it.
6. **Callback fees** are billed to the pool in native ETH on Base Sepolia (via `pay`/debt). The deploy script
   sends 0.02 ETH to the pool and 0.1 of the native Lasna token to the RC; both amounts are guesses. `coverDebt()` is
   exposed on both contracts. The RC is not tested against the real debt/`pay` flow.
7. **Gas.** The measured `react()` gas (mean 94k, maximum 128k over the parity vectors, no callback request;
   92k later swaps and 129k for the first swap in the dedicated gas test) comes from Foundry, with the stub
   system contract. Weights are immutables in the RC's code, not storage slots (cheaper to read; the brief
   allowed constructor-set weights). Gas accounting on Reactive may differ. The limit
   is 900,000.
8. **Latency.** Unmeasured. Lasna block time and callback delivery time are unknown; `scripts/demo.py`
   measures them on the first live run.
9. **Timestamps.** The RC uses the `timestamp` field of the `Swap` event (`block.timestamp` on Base Sepolia,
   1 s resolution; blocks are 200 ms, so several swaps can share a second and `dt = 0` is handled).

## Deployment and tooling

10. **Lasna deployment flags.** `scripts/deploy.sh` passes `--legacy` for Lasna and nothing special for Base
    Sepolia. Whether Lasna needs it was not checked.
11. **Explorer links.** `docs/RUN.md` (written by the demo) links to `sepolia.basescan.org` and
    `lasna.reactscan.net`; the second URL pattern is from memory.
12. **`scripts/deploy.sh`, `scripts/demo.py` and `scripts/watch.py` have not been run against a network.** The
    demo's `--dry-run` mode and the Python pieces were exercised offline. The Foundry tests exercise the same
    contract paths with the stub.
13. **Address prediction.** The pool's address is computed as the deployer's next CREATE address on Base
    Sepolia. Any other transaction from the deployer between `DeployTokens` and `DeployPool` breaks it; the pool
    script then aborts and the RC must be redeployed.

## Libraries

14. **reactive-lib-omni is `SPDX-License-Identifier: UNLICENSED`** in every source file and the repository has
    no LICENSE file. This repository does not copy or redistribute it: `lib/` is gitignored and the library is
    installed with `forge install`. Check its terms before redistributing builds. It was installed from the
    default branch (reported as v0.1.0) without pinning a commit; pin one when you commit.
15. forge-std is MIT / Apache-2.0 (dual), used only in tests and scripts.

## Model and data

16. **All training data is simulated** by `ml/sim.py`. Regime parameters (trade sizes, gaps, the share of
    whale swaps that are later unwound) are invented, not calibrated on real pools. Reported rates say how the
    model behaves on this simulator, not on mainnet.
17. **Family C generalisation is within the simulator.** The three attack families all end by unwinding a
    position, and the unwinding feature (`reversal`) is shared. Held-out C therefore tests a different build-up
    (many small swaps instead of one big one), not a different mechanism.
18. **Labels mark unwinding legs only.** A guard that fires earlier counts as a false positive per swap and as
    `early` per attack.
19. **Slow arbitrage-back only.** The simulator never reverses a whale swap within 25 s except in attacks, so
    it cannot say how the model treats fast arbitrage of a large swap.
20. **Rolling state is stored in 64-bit fields** (`flow`, `volume`, `rate`). They grow by at most 2^16 per
    swap; overflow would need about 2^47 swaps with no time passing, so it is not guarded.
21. **One pool per RC.** The RC scores a single pool address; state is not keyed per pool.
