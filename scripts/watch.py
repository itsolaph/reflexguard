#!/usr/bin/env python3
"""Tail the Scored events of the Reactive Contract on Lasna. Standard library + `cast` only.

    export LASNA_RPC=https://lasna-rpc.rnk.dev/
    python3 scripts/watch.py [--from-block N] [--rc 0x...] [--once]

One line per scored swap: block, score bar, the six features, and the origin transaction hash. Lines at or
above the RC's halt threshold are marked HALT.
"""
import argparse
import sys
import time

import common as C

FEATURES = ("size", "reversal", "res.drift", "rate", "volume", "px.dev")


def render(row, threshold):
    score, packed, tx, ts = row["words"][:4]
    bar = "#" * (score * 20 // 10000)
    feats = " ".join(f"{name}={v / 1024:+.2f}" for name, v in zip(FEATURES, C.unpack_features(packed)))
    mark = "HALT" if score >= threshold else "    "
    return f"{row['block']:>9} {score:>5} |{bar:<20}| {mark} {feats}  origin tx 0x{tx:064x}"[:230]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rc", help="reactive contract address (default: deployments.json)")
    ap.add_argument("--from-block", type=int, help="first block to show (default: current block)")
    ap.add_argument("--interval", type=float, default=1.0, help="poll interval in seconds")
    ap.add_argument("--once", action="store_true", help="print existing events and exit")
    args = ap.parse_args()

    rpc = C.env("LASNA_RPC")
    rc = args.rc or C.load_deployments()["rc"]
    threshold = int(C.call(rpc, rc, "threshold()(uint256)").split()[0])
    start = args.from_block if args.from_block is not None else C.block_number(rpc)
    print(f"watching {rc} from block {start}, halt threshold {threshold}", file=sys.stderr)
    shown = set()
    while True:
        for row in C.logs(rpc, rc, C.SCORED_SIG, start):
            key = (row["block"], row["index"])
            if key not in shown:
                shown.add(key)
                print(render(row, threshold), flush=True)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
