"""Train, quantise and evaluate the ReflexGuard model; write weights, report, vectors and demo plan.

    .venv/bin/python ml/train.py

Two runs share the same simulated scenarios and splits:
  1. generalisation run: train on benign + A + B only, evaluate on held-out family C;
  2. final run: train on benign + A + B + C, ship these weights.
Everything is seeded; the output is reproducible byte for byte on the same numpy version.
"""
import json
import os
import random

import numpy as np

import features as F
import metrics as M
import report as R
import sim
import vectors as V
from mlp import FloatMLP

SEED = 7
N_BENIGN_EACH = 700       # scenarios per benign regime
N_ATTACK_EACH = 500       # scenarios per attack family
FALSE_HALT_TARGET = 0.02  # max share of benign validation scenarios that may contain a halt
RESTARTS = 3
HERE = os.path.dirname(os.path.abspath(__file__))


def featurize(scenarios):
    for sc in scenarios:
        st = F.State()
        sc["feats"] = [F.update(st, z2o, a_in, r0, r1, ts) for z2o, a_in, _o, r0, r1, ts in sc["events"]]
    return scenarios


def split(scenarios, rng):
    """60/20/20 per scenario kind."""
    parts = {"train": [], "val": [], "test": []}
    kinds = sorted({sc["kind"] for sc in scenarios})
    for kind in kinds:
        group = [sc for sc in scenarios if sc["kind"] == kind]
        rng.shuffle(group)
        a, b = int(0.6 * len(group)), int(0.8 * len(group))
        parts["train"] += group[:a]
        parts["val"] += group[a:b]
        parts["test"] += group[b:]
    return parts


def stack(scenarios):
    x = np.array([f for sc in scenarios for f in sc["feats"]], dtype=float) / 1024.0
    y = np.array([l for sc in scenarios for l in sc["labels"]], dtype=float)
    return x, y


def int_scores(scenarios, weights):
    return {sc["id"]: [F.mlp(f, weights) for f in sc["feats"]] for sc in scenarios}


def choose_threshold(val, scores, families):
    """Highest recall (mean episode catch rate over `families`) with benign false halts under the target."""
    best = None
    for thr in range(3000, 9991, 50):
        s = M.summarise(M.episodes(val, scores, thr))
        fh = s["benign_false_halt_rate"]
        recall = float(np.mean([s[f]["caught"] for f in families]))
        key = (fh <= FALSE_HALT_TARGET, recall if fh <= FALSE_HALT_TARGET else -fh, thr)
        if best is None or key > best[0]:
            best = (key, thr)
    return best[1]


def run(parts, train_families, label):
    """Train on benign + `train_families`, return model details and metrics for every split and family."""
    seen = ("benign", *train_families)
    train = [sc for sc in parts["train"] if sc["family"] in seen]
    val = [sc for sc in parts["val"] if sc["family"] in seen]
    x, y = stack(train)
    best = None
    for k in range(RESTARTS):
        rng = np.random.default_rng(SEED + k)
        net = FloatMLP(rng)
        net.fit(x, y, rng)
        w = net.quantise()
        sc_val = int_scores(val, w)
        thr = choose_threshold(val, sc_val, train_families)
        s = M.summarise(M.episodes(val, sc_val, thr))
        recall = float(np.mean([s[f]["caught"] for f in train_families]))
        objective = (s["benign_false_halt_rate"] <= FALSE_HALT_TARGET, recall)
        print(f"[{label}] restart {k}: threshold {thr}, val false-halt {s['benign_false_halt_rate']:.3f}, val recall {recall:.3f}")
        if best is None or objective > best[0]:
            best = (objective, net, w, thr)
    _, net, w, thr = best

    result = {"label": label, "train_families": list(train_families), "weights": w, "threshold": thr, "splits": {}}
    for name, scs_all in parts.items():
        scs = [sc for sc in scs_all if sc["family"] in seen]
        scores = int_scores(scs, w)
        flat_s = [v for sc in scs for v in scores[sc["id"]]]
        flat_y = [l for sc in scs for l in sc["labels"]]
        per_family = {}
        for fam in seen:
            sub = [sc for sc in scs if sc["family"] == fam]
            if sub:
                per_family[fam] = M.per_swap([v for sc in sub for v in scores[sc["id"]]],
                                             [l for sc in sub for l in sc["labels"]], thr)
        xs, _ = stack(scs)
        float_scores = net.predict(xs) * 10000
        agree = float(np.mean((float_scores >= thr) == (np.array(flat_s) >= thr)))
        result["splits"][name] = {
            "n_swaps": len(flat_y), "n_pos": int(sum(flat_y)),
            "swap": M.per_swap(flat_s, flat_y, thr),
            "episodes": M.summarise(M.episodes(scs, scores, thr)),
            "float_int_agreement": agree,
            "float_int_mean_abs_diff": float(np.mean(np.abs(float_scores - np.array(flat_s)))),
            "float_int_max_abs_diff": float(np.max(np.abs(float_scores - np.array(flat_s)))),
            "float_auc": M.auc(float_scores, flat_y),
            "by_family": per_family,
        }
    held = [sc for scs in parts.values() for sc in scs if sc["family"] not in seen]
    if held:
        scores = int_scores(held, w)
        result["heldout"] = {
            "n": len(held),
            "swap": M.per_swap([v for sc in held for v in scores[sc["id"]]], [l for sc in held for l in sc["labels"]], thr),
            "episodes": M.summarise(M.episodes(held, scores, thr)),
        }
    result["scores_test"] = int_scores([sc for sc in parts["test"] if sc["family"] in seen], w)
    return result


def a_gap_breakdown(scenarios, scores, thr):
    """Recall of family A by time between the pump and the first unwind leg."""
    bins = [(0, 3), (3, 6), (6, 12), (12, 21)]
    rows = []
    for lo, hi in bins:
        hit = n = 0
        for sc in scenarios:
            if sc["family"] != "A":
                continue
            first = min(sc["reversal"])
            gap = sc["events"][first][5] - sc["events"][first - 1][5]
            if lo <= gap < hi:
                n += 1
                hit += any(scores[sc["id"]][i] >= thr for i in sc["reversal"])
        rows.append((f"{lo}-{hi} s", n, hit))
    return rows


def demo_check(w, thr, gap_choices, trials=300):
    """Replay the demo plan with every gap drawn from `gap_choices` (seconds); count false halts and catches."""
    plan = sim.demo_plan()
    rng = random.Random(SEED)
    out = {"whale_halted": 0, "attack_halted": 0, "dump1_halted": 0, "drip_halted": 0, "drip_before_reverse": 0,
           "trials": trials}
    for _ in range(trials):
        for part in ("warmup+attack_a", "drip_c"):
            events = V.demo_events(plan, part, lambda: rng.choice(gap_choices))
            res = F.score_sequence(events, w)
            scores = [r[1] for r in res]
            if part == "drip_c":
                out["drip_halted"] += max(scores) >= thr
                out["drip_before_reverse"] += max(scores[:-1]) >= thr
            else:
                n_warm = len(plan["warmup"])
                out["whale_halted"] += max(scores[:n_warm]) >= thr
                out["attack_halted"] += max(scores[n_warm:]) >= thr
                out["dump1_halted"] += scores[n_warm + 1] >= thr
    return out


def vector_sequences(scenarios, rng):
    """Edge cases, demo sequences and a spread of simulated scenarios."""
    seqs = dict(V.edge_sequences())
    plan = sim.demo_plan()
    seqs["demo_warmup_whale"] = V.demo_events(plan, "warmup", lambda: plan["gap"])
    seqs["demo_attack_a"] = V.demo_events(plan, "warmup+attack_a", lambda: plan["gap"])
    seqs["demo_drip_c"] = V.demo_events(plan, "drip_c", lambda: plan["gap"])
    kinds = ["retail", "whale", "arb", "burst", "pump_dump", "sandwich", "drip"]
    for kind in kinds:
        pool = [sc for sc in scenarios if sc["kind"] == kind]
        take = 4 if sc_is_benign(kind) else 6
        for i, sc in enumerate(rng.sample(pool, take)):
            seqs[f"{kind}_{i}"] = sc["events"]
    return seqs


def sc_is_benign(kind):
    return kind in ("retail", "whale", "arb", "burst")


def write_json(name, obj):
    with open(os.path.join(HERE, name), "w") as fh:
        json.dump(obj, fh, indent=1 if name != "vectors.json" else None)
        fh.write("\n")


def main():
    scenarios = featurize(sim.generate(SEED, N_BENIGN_EACH, N_ATTACK_EACH))
    parts = split(scenarios, random.Random(SEED))

    gen = run(parts, ("A", "B"), "generalisation (C held out)")
    final = run(parts, ("A", "B", "C"), "final (all families)")
    w, thr = final["weights"], final["threshold"]

    breakdown = a_gap_breakdown(parts["test"], final["scores_test"], thr)
    demo = {"narrow": demo_check(w, thr, (2, 3, 4)), "wide": demo_check(w, thr, tuple(range(0, 13)))}

    write_json("weights.json", {"weights": w, "packed": ["0x%064x" % x for x in F.pack_weights(w)],
                                 "threshold": thr, "format": "W1[8][6] Q8, b1[8] Q10, W2[8] Q8, b2 Q10"})
    plan = sim.demo_plan()
    write_json("demo_plan.json", V.plan_json(plan))
    V.write(os.path.join(HERE, "vectors.json"), vector_sequences(scenarios, random.Random(SEED)), w)
    R.write(os.path.join(HERE, "report.md"), gen, final, breakdown, demo, parts,
            {"seed": SEED, "benign_each": N_BENIGN_EACH, "attack_each": N_ATTACK_EACH, "target": FALSE_HALT_TARGET})
    print(f"threshold {thr}; wrote weights.json, vectors.json, demo_plan.json, report.md")


if __name__ == "__main__":
    main()
