# Progress

- Installed forge-std and reactive-lib-omni (no-git, under lib/, gitignored). Read AbstractReactive/AbstractCallback.
- Docs check: Reactive react() is an ordinary transaction (no ReactVM storage split described), so owner-set threshold works. Recorded in docs/ASSUMPTIONS.md.
- ml/features.py: integer reference (state, 6 features, MLP, sigmoid LUT). ml/gen_tables.py writes src/reactive/ReflexTables.sol.
- src/origin (MockToken, GuardedPool) and src/reactive/ReflexGuardRC.sol written; builds.
- Parity test (test/Parity.t.sol) green on edge vectors with throwaway random weights: exact score and features.
- Packed RC state into two storage slots; parity re-checked.
- ml/sim.py (4 benign regimes, families A/B/C), ml/mlp.py (numpy backprop), ml/train.py, report, vectors, demo plan.
- First labelling (drip swaps positive) gave a poor model; labels now mark only unwinding legs. Final model: AUC 0.9993, held-out C caught 97.4%.
- test/Guard.t.sol: end-to-end with the real pool (benign whale, pump and dump, callback execution, auth, resume, retry, malformed logs, gas). 19 forge tests green.
- script/Deploy*.s.sol, scripts/{deploy.sh,demo.py,watch.py,common.py}. demo.py --dry-run exercised; no live run possible here.
- README, docs/SPEC.md, docs/ASSUMPTIONS.md, LICENSE, HANDOFF.md written.
