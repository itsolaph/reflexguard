# Handoff

Nothing is committed, pushed, deployed or broadcast. `lib/` (forge-std, reactive-lib-omni) is gitignored and was
installed with `forge install --no-git` (no vendoring: reactive-lib-omni is `UNLICENSED`, see
`docs/ASSUMPTIONS.md` item 14). To commit with pinned dependencies as submodules:

```
# remove the "lib/" line from .gitignore, then:
rm -rf lib
forge install foundry-rs/forge-std Reactive-Network/reactive-lib-omni     # git submodules, commits pinned
```

The README setup command (`--no-git`) is for readers who just want to build.

## Done

- Contracts: `src/origin/{MockToken,GuardedPool}.sol`, `src/reactive/{ReflexGuardRC,ReflexTables}.sol`.
- ML: `ml/{sim,features,mlp,metrics,train,report,vectors,gen_tables}.py`, outputs `weights.json`,
  `report.md`, `vectors.json`, `demo_plan.json`. `train.py` is deterministic (same md5 on two runs).
- Tests: `forge test -vv` -> **19 passed, 0 failed** (1 parity test over 46 sequences / 1,287 swaps with exact
  score and feature equality; 18 end-to-end / auth / robustness / gas tests). `python -m unittest discover -s
  ml` -> 11 passed.
- Scripts: `script/Deploy{Tokens,Reactive,Pool}.s.sol`, `scripts/{deploy.sh,demo.py,watch.py,common.py}`.
- Docs: `README.md`, `docs/SPEC.md`, `docs/ASSUMPTIONS.md`, `LICENSE` (MIT), `PROGRESS.md`.

## Key numbers (integer model as deployed, threshold 8300)

| | |
|---|---|
| per-swap AUC, test split | 0.9993 |
| per-swap precision / recall / FPR, test | 95.9% / 81.3% / 0.045% (17 of 37,778 negative-labelled swaps; 16 of 33,721 in benign scenarios) |
| benign scenarios with a false halt, test | 2.9% (burst regime 14/140, whale 2/140) |
| attacks halted during unwind, test: A / B / C | 85% / 86% / 95% |
| **held-out family C** (trained without C, threshold 7250), n = 500 | **97.4% caught** (CI 96-98%), per-swap recall on unwinding legs 87.5%, 0.0% flagged before the unwind |
| float vs int decision agreement | 100%, mean score difference 0.9 / 10000 |
| `react()` gas (Foundry `lastFrameGas`, no callback request) | mean 94k, max 128k, first swap 129k (limit 900k) |
| demo plan under random gaps (2-4 s, and 0-12 s), 300 trials each | whale false halts 0/300 in both; pump-and-dump halted 300/300 (already on the first dump leg); drip halted 300/300, only on the reversal swap |

The honest weak points are in `ml/report.md` section 4 and the README Limits: the opening leg of an attack is
never flagged, the burst regime produces most false halts, everything is synthetic.

## Before deploying

0. `demo.py` always sends `resume()` first (clears the guard state), so it can be re-run on the same deployment.
1. Fund one account on Base Sepolia (ETH) and Lasna (native token). The deploy script sends `POOL_FUND`
   (default 0.02 ETH) to the pool and `RC_FUND` (default 0.1 native) to the RC; these are guesses.
2. `forge test` must be green first (it reads `ml/weights.json`, the deploy script does too).
3. If you retrain, nothing on chain can be updated: weights are immutable in the RC. Redeploy.

## Commands

```
export PRIVATE_KEY=0x...                      # 0x-prefixed hex
export BASE_SEPOLIA_RPC=https://...
export LASNA_RPC=https://lasna-rpc.rnk.dev/
scripts/deploy.sh                             # writes deployments.json; fill README "Deployed addresses"
python3 scripts/demo.py --dry-run             # offline check of plan and expected scores
python3 scripts/watch.py                      # terminal 2: tail Scored events
python3 scripts/demo.py                       # live run, writes docs/RUN.md (latency table, tx hashes)
```

`deploy.sh` order: tokens -> predict pool address (deployer nonce) -> RC on Lasna with `--legacy` -> pool on
Base Sepolia, aborting if the address differs. The deployer must not send other Base Sepolia transactions in
between.

## Things I could not verify (need a look on first live run)

- Whether `react()` really sees owner-set state (threshold) on Omni; whether `LogRecord.txHash` is the origin
  tx hash; whether `--legacy` is needed on Lasna; the Lasna explorer URL pattern in `scripts/demo.py`.
- `demo.py` has only been run with `--dry-run`. If the first live run misbehaves, suspects in order: `cast logs`
  JSON shape on the Lasna RPC, log range limits on `--from-block`, block timestamp parsing.
- Real callback latency; the demo paces swaps 3 s apart and the second dump leg may or may not be blocked by the
  halt.
- I could not start `anvil` in this environment, so there was no local end-to-end rehearsal of the scripts.
