"""Parity vectors: event sequences with the expected integer output of the reference pipeline.

The file is flat (parallel arrays plus `seqStart`) so Foundry can read it with the JSON cheatcodes.
Large integers are decimal strings.
"""
import json

import features as F
import sim

MAXU = (1 << 256) - 1


def edge_sequences():
    """Hand-written sequences that exercise clipping, skipping and degenerate timing."""
    e18 = 10 ** 18
    pool = (1_000_000 * e18, 1_000_000 * e18)
    seqs = {}

    def swap(z2o, amt, r0, r1, ts):
        return (z2o, amt, 0, r0, r1, ts)

    seqs["edge_basic"] = [swap(i % 2 == 0, 100 * e18, pool[0] + i, pool[1] - i, 1000 + 7 * i) for i in range(6)]
    seqs["edge_same_second"] = [swap(True, 20_000 * e18, pool[0] + i * 20_000 * e18, pool[1] - i * 19_000 * e18, 5000) for i in range(10)]
    seqs["edge_huge_gaps"] = [swap(i % 3 != 0, 5_000 * e18, pool[0], pool[1], 100 + i * 10 ** 6) for i in range(5)]
    seqs["edge_amount_over_reserve"] = [
        swap(True, 5 * pool[0], pool[0], pool[1], 10),
        swap(False, MAXU, pool[0], pool[1], 11),
        swap(True, 1, 1, 1, 12),
    ]
    seqs["edge_reserve_clip"] = [
        swap(True, 1 << 100, 1 << 101, 1 << 100, 50),
        swap(False, 1 << 99, 1 << 101, 1 << 101, 51),
        swap(True, 1 << 98, 1 << 120, 1, 52),
        swap(False, 1 << 98, 1, 1 << 120, 53),
        swap(True, 1, 1, 1 << 96, 54),
        swap(False, 1, 1 << 96, 1, 55),
    ]
    seqs["edge_zero_reserve_skipped"] = [
        swap(True, 100 * e18, pool[0], pool[1], 100),
        swap(True, 100 * e18, 0, pool[1], 101),
        swap(False, 100 * e18, pool[0], 0, 102),
        swap(False, 100 * e18, pool[0], pool[1], 103),
    ]
    seqs["edge_timestamp_goes_backwards"] = [
        swap(True, 50_000 * e18, pool[0], pool[1], 1000),
        swap(False, 50_000 * e18, pool[0], pool[1], 990),
        swap(True, 50_000 * e18, pool[0], pool[1], 1005),
    ]
    seqs["edge_timestamp_overflow"] = [
        swap(True, 100 * e18, pool[0], pool[1], 1 << 70),
        swap(False, 100 * e18, pool[0], pool[1], MAXU),
        swap(True, 100 * e18, pool[0], pool[1], 5),
    ]
    seqs["edge_price_floor"] = [
        swap(True, 1, 1 << 100, 1, 10),
        swap(False, 1, 1 << 100, 5, 20),
        swap(True, 1, 1 << 100, 1 << 60, 30),
    ]
    return seqs


def demo_events(plan, part, gap_fn, t0=1_760_000_000):
    """Event list for part of the demo plan, as the pool would emit it.

    part is "warmup", "warmup+attack_a" or "drip_c". The pool state carries over from the earlier phases
    (the demo resumes the pool between the attack and the drip), only the guard state starts fresh.
    """
    pool = sim.Pool(sim.DEMO_LIQUIDITY, sim.DEMO_LIQUIDITY)
    t = t0
    events = []
    phases = {"warmup": ["warmup"], "warmup+attack_a": ["warmup", "attack_a"], "drip_c": ["warmup", "attack_a", "drip_c"]}[part]
    for phase in phases:
        for _label, z2o, amount in plan[phase]:
            t += gap_fn()
            out = pool.swap(z2o, amount)
            events.append((z2o, amount, out, pool.r0, pool.r1, t))
        if part == "drip_c" and phase == "attack_a":
            events = []     # the guard restarts after the resume
    return events


def plan_json(plan):
    """Flat-array form of the demo plan for Foundry and scripts/demo.py."""
    out = {"gap": plan["gap"], "liquidity": str(sim.DEMO_LIQUIDITY)}
    for phase in ("warmup", "attack_a", "drip_c"):
        out[phase] = {"label": [s[0] for s in plan[phase]],
                      "zeroForOne": [1 if s[1] else 0 for s in plan[phase]],
                      "amountIn": [str(s[2]) for s in plan[phase]]}
    return out


def build(sequences, weights):
    """sequences: dict name -> list of (z2o, amount_in, amount_out, r0, r1, ts)."""
    out = {k: [] for k in ["zeroForOne", "amountIn", "amountOut", "reserve0", "reserve1", "timestamp",
                           "expectedEmitted", "expectedScore", "expectedFeatures"]}
    out["seqStart"] = []
    out["seqName"] = []
    for name, events in sequences.items():
        out["seqStart"].append(len(out["zeroForOne"]))
        out["seqName"].append(name)
        for ev, res in zip(events, F.score_sequence(events, weights)):
            z2o, amt_in, amt_out, r0, r1, ts = ev
            out["zeroForOne"].append(1 if z2o else 0)
            for key, val in (("amountIn", amt_in), ("amountOut", amt_out), ("reserve0", r0),
                             ("reserve1", r1), ("timestamp", ts)):
                out[key].append(str(val))
            out["expectedEmitted"].append(0 if res is None else 1)
            out["expectedScore"].append(0 if res is None else res[1])
            out["expectedFeatures"].append("0" if res is None else str(F.pack_features(res[0])))
    out["seqStart"].append(len(out["zeroForOne"]))
    return out


def write(path, sequences, weights):
    with open(path, "w") as fh:
        json.dump(build(sequences, weights), fh)
