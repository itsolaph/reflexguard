#!/usr/bin/env python3
"""Live demo of ReflexGuard on the deployed pool and Reactive Contract. Standard library + `cast` only.

    export PRIVATE_KEY=0x... BASE_SEPOLIA_RPC=https://... LASNA_RPC=https://lasna-rpc.rnk.dev/
    python3 scripts/demo.py            # run the demo, write docs/RUN.md
    python3 scripts/demo.py --dry-run  # no network: print the plan and the scores the model expects

Phases (amounts and gaps come from ml/demo_plan.json):
  1. benign swaps including one 6% whale swap  -> must not trigger
  2. pump-and-dump (family A, in the training set)       -> Scored on Lasna, Halted on Base Sepolia
  3. owner resumes the pool, then a slow drip + reversal (family C, held out in the generalisation run)
After each attack a final swap is attempted and must revert. Every on-chain score is recomputed here with the
Python integer reference and compared.
"""
import argparse
import datetime
import json
import os
import threading
import time

import common as C

BASE_EXPLORER = "https://sepolia.basescan.org"
LASNA_EXPLORER = "https://lasna.reactscan.net"
MINT = 10_000_000 * 10 ** 18
MAX_UINT = 2 ** 256 - 1


def utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%H:%M:%S")


def load_plan():
    with open(os.path.join(C.ROOT, "ml", "demo_plan.json")) as fh:
        plan = json.load(fh)
    phases = {}
    for name in ("warmup", "attack_a", "drip_c"):
        p = plan[name]
        phases[name] = [(p["label"][i], p["zeroForOne"][i] == 1, int(p["amountIn"][i])) for i in range(len(p["label"]))]
    return plan["gap"], int(plan["liquidity"]), phases


def dry_run():
    gap, liquidity, phases = load_plan()
    w = C.load_weights()
    pool = [liquidity, liquidity]
    t = 1_760_000_000
    print(f"threshold {w['threshold']}, planned gap {gap} s\n")
    print(f"{'phase':9} {'step':8} {'dir':6} {'amount_in':>28}   expected score")
    state = C.F.State()
    for phase, steps in phases.items():
        if phase == "drip_c":
            state = C.F.State()      # the guard restarts after the resume that precedes the drip
        for label, z2o, amount in steps:
            t += gap
            r_in, r_out = (pool[0], pool[1]) if z2o else (pool[1], pool[0])
            out = amount * 997 * r_out // (r_in * 1000 + amount * 997)
            if z2o:
                pool[0] += amount
                pool[1] -= out
            else:
                pool[1] += amount
                pool[0] -= out
            f = C.F.update(state, z2o, amount, pool[0], pool[1], t)
            score = C.F.mlp(f, w["weights"])
            flag = "  <-- halt" if score >= w["threshold"] else ""
            print(f"{phase:9} {label:8} {'0->1' if z2o else '1->0':6} {amount:>28}   {score:5d}{flag}")


class Demo:
    def __init__(self):
        self.dep = C.load_deployments()
        self.base = C.env("BASE_SEPOLIA_RPC")
        self.lasna = C.env("LASNA_RPC")
        self.key = C.env("PRIVATE_KEY")
        self.gap, self.liquidity, self.phases = load_plan()
        self.weights = C.load_weights()
        self.sender = C.cast("wallet", "address", "--private-key", self.key)
        self.pool, self.rc = self.dep["pool"], self.dep["rc"]
        self.base_start = self.lasna_start = 0
        self.steps = []                 # every swap the demo sent
        self.seen = {}                  # (kind, tx hash int) -> first wall-clock time the watcher saw it
        self.stop = threading.Event()
        self.report = []                # markdown lines for docs/RUN.md

    # ------------------------------------------------------------ plumbing
    def log(self, text=""):
        print(text, flush=True)
        self.report.append(text)

    def watcher(self):
        """Record when Scored (Lasna) and Halted (Base Sepolia) first become visible."""
        while not self.stop.is_set():
            try:
                for r in C.logs(self.lasna, self.rc, C.SCORED_SIG, self.lasna_start):
                    self.seen.setdefault(("scored", r["words"][2]), time.time())
                for r in C.logs(self.base, self.pool, C.HALTED_SIG, self.base_start):
                    self.seen.setdefault(("halted", r["words"][1], r["block"]), time.time())
            except C.CastError:
                pass
            self.stop.wait(0.5)

    def swap(self, label, z2o, amount):
        t_send = time.time()
        step = {"label": label, "z2o": z2o, "amount": amount, "sent": t_send}
        try:
            r = C.send(self.base, self.key, self.pool, "swap(bool,uint256,uint256,address)",
                       "true" if z2o else "false", amount, 0, self.sender)
            step.update(tx=r["transactionHash"], block=C.to_int(r["blockNumber"]), confirmed=time.time())
            step["ts"] = C.block_timestamp(self.base, step["block"])
            self.log(f"| {label} | {'token0->token1' if z2o else 'token1->token0'} | {amount / 1e18:,.1f} | "
                     f"[`{step['tx'][:10]}`]({BASE_EXPLORER}/tx/{step['tx']}) | {utc(step['ts'])} |")
        except C.CastError as e:
            step.update(tx=None, error=str(e).splitlines()[-1][:160])
            self.log(f"| {label} | {'token0->token1' if z2o else 'token1->token0'} | {amount / 1e18:,.1f} | "
                     f"reverted: `{step['error']}` | - |")
        self.steps.append(step)
        return step

    def play(self, phase):
        self.log("| step | direction | amount in (tokens) | tx | block time (UTC) |")
        self.log("|---|---|---|---|---|")
        last = None
        for label, z2o, amount in self.phases[phase]:
            if last is not None:
                time.sleep(max(0.0, self.gap - (time.time() - last)))
            last = time.time()
            self.swap(label, z2o, amount)
        self.log()

    def wait_for(self, predicate, timeout, what):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                value = predicate()
            except C.CastError:      # one RPC hiccup should not end a live run
                value = None
            if value:
                return value
            time.sleep(0.5)
        self.log(f"*timed out waiting for {what}*")
        return None

    # ------------------------------------------------------------ steps
    def prepare(self):
        self.log("## Setup\n")
        for token in (self.dep["token0"], self.dep["token1"]):
            C.send(self.base, self.key, token, "mint(address,uint256)", self.sender, MINT)
            C.send(self.base, self.key, token, "approve(address,uint256)", self.pool, MAX_UINT)
        # Always resume, even if the pool is not paused: the Resumed log clears the guard's rolling state, so
        # every run starts from the empty state the plan and the Python comparison assume.
        self.lasna_start = C.block_number(self.lasna)
        r = C.send(self.base, self.key, self.pool, "resume()")
        self.base_start = C.to_int(r["blockNumber"])
        if not self.wait_for(lambda: C.logs(self.lasna, self.rc, C.REARMED_SIG, self.lasna_start), 90,
                             "Rearmed on Lasna (guard state cleared)"):
            self.log("*the guard may still hold state from an earlier run; scores can differ from the plan*\n")
        threshold = C.call(self.lasna, self.rc, "threshold()(uint256)").split()[0]
        self.log(f"- pool [`{self.pool}`]({BASE_EXPLORER}/address/{self.pool}) on Base Sepolia")
        self.log(f"- reactive contract [`{self.rc}`]({LASNA_EXPLORER}/address/{self.rc}) on Lasna")
        self.log(f"- halt threshold: {threshold} (score range 0-10000)\n")
        threading.Thread(target=self.watcher, daemon=True).start()

    def latency_table(self, title, phase_first_block):
        """Find the first halt after `phase_first_block`, then print swap -> Scored -> Halted timings."""
        self.log(f"### {title}: latency\n")
        halted = self.wait_for(lambda: [h for h in C.logs(self.base, self.pool, C.HALTED_SIG, phase_first_block)][:1],
                               90, "Halted on Base Sepolia")
        if not halted:
            return None
        h = halted[0]
        trigger = h["words"][1]
        step = next((s for s in self.steps if s.get("tx") and int(s["tx"], 16) == trigger), None)
        scored = self.wait_for(lambda: [r for r in C.logs(self.lasna, self.rc, C.SCORED_SIG, self.lasna_start)
                                        if r["words"][2] == trigger][:1], 30, "Scored on Lasna")
        if not step or not scored:
            self.log("*trigger swap or Scored event not found*\n")
            return None
        s = scored[0]
        t_scored = C.block_timestamp(self.lasna, s["block"])
        t_halted = h["words"][2]
        seen_scored = self.seen.get(("scored", trigger))
        seen_halted = self.seen.get(("halted", trigger, h["block"]))
        wall_scored = "n/a" if not seen_scored else "%.1f s" % (seen_scored - step["confirmed"])
        wall_halted = "n/a" if not seen_halted else "%.1f s" % (seen_halted - step["confirmed"])
        self.log("| stage | chain | tx | timestamp (UTC) | after swap (block time) | after swap (first seen by this script) |")
        self.log("|---|---|---|---|---|---|")
        self.log(f"| trigger swap `{step['label']}` | Base Sepolia | [`{step['tx'][:10]}`]({BASE_EXPLORER}/tx/{step['tx']}) | "
                 f"{utc(step['ts'])} | 0 s | 0 s |")
        self.log(f"| Scored (score {s['words'][0]}) | Lasna | [`{s['tx'][:10]}`]({LASNA_EXPLORER}/tx/{s['tx']}) | "
                 f"{utc(t_scored)} | {t_scored - step['ts']} s | {wall_scored} |")
        self.log(f"| Halted | Base Sepolia | [`{h['tx'][:10]}`]({BASE_EXPLORER}/tx/{h['tx']}) | "
                 f"{utc(t_halted)} | {t_halted - step['ts']} s | {wall_halted} |")
        self.log("\nBlock times have 1 s resolution. The last column is wall-clock from the polling thread "
                 "(0.5 s poll, RPC latency included), measured from the moment `cast` returned the swap receipt.\n")
        return h

    def attempt_blocked_swap(self):
        self.log("### Swap attempted after the halt\n")
        try:
            C.send(self.base, self.key, self.pool, "swap(bool,uint256,uint256,address)", "true", 10 ** 18, 0, self.sender)
            self.log("**The swap went through: the pool was not paused.**\n")
        except C.CastError as e:
            reason = " ".join(str(e).splitlines()[-1:])[:200]
            self.log(f"The swap reverted as expected: `{reason}`\n")

    def resume(self):
        self.log("### Owner resumes the pool\n")
        before = len(C.logs(self.lasna, self.rc, C.REARMED_SIG, self.lasna_start))
        r = C.send(self.base, self.key, self.pool, "resume()")
        self.log(f"[`{r['transactionHash'][:10]}`]({BASE_EXPLORER}/tx/{r['transactionHash']}): `Resumed` emitted.")
        ok = self.wait_for(lambda: len(C.logs(self.lasna, self.rc, C.REARMED_SIG, self.lasna_start)) > before, 60,
                           "Rearmed on Lasna")
        self.log("The Reactive Contract re-armed itself from the `Resumed` log.\n" if ok else "")

    def parity(self):
        """Recompute every on-chain score from the pool's Swap logs with the Python integer reference."""
        self.log("## On-chain score vs Python integer reference\n")
        swaps = C.logs(self.base, self.pool, C.SWAP_SIG, self.base_start)
        resumed = C.logs(self.base, self.pool, C.RESUMED_SIG, self.base_start)
        scored = {r["words"][2]: r for r in C.logs(self.lasna, self.rc, C.SCORED_SIG, self.lasna_start)}
        timeline = sorted([("swap", r) for r in swaps] + [("resume", r) for r in resumed],
                          key=lambda x: (x[1]["block"], x[1]["index"]))
        labels = {int(s["tx"], 16): s["label"] for s in self.steps if s.get("tx")}
        state = C.F.State()
        self.log("| swap | tx | on-chain score | python score | match |")
        self.log("|---|---|---|---|---|")
        mismatches = 0
        for kind, r in timeline:
            if kind == "resume":
                state.reset()
                continue
            z2o, amount_in, _out, r0, r1, ts = r["words"]
            f = C.F.update(state, z2o != 0, amount_in, r0, r1, ts)
            expected = C.F.mlp(f, self.weights["weights"])
            chain = scored.get(int(r["tx"], 16))
            if chain is None:
                self.log(f"| {labels.get(int(r['tx'], 16), '?')} | `{r['tx'][:10]}` | not scored (yet) | {expected} | - |")
                continue
            ok = chain["words"][0] == expected and C.unpack_features(chain["words"][1]) == f
            mismatches += not ok
            self.log(f"| {labels.get(int(r['tx'], 16), '?')} | `{r['tx'][:10]}` | {chain['words'][0]} | {expected} | "
                     f"{'yes' if ok else '**NO**'} |")
        self.log(f"\nMismatches: {mismatches}.\n")

    # ------------------------------------------------------------ main flow
    def run(self, with_drip=True):
        self.log("# ReflexGuard demo run\n")
        self.log(f"Run started {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC.\n")
        self.prepare()

        self.log("## 1. Benign flow including a whale swap\n")
        self.play("warmup")
        self.log("No halt expected. Halted events so far: "
                 f"{len(C.logs(self.base, self.pool, C.HALTED_SIG, self.base_start))}.\n")

        self.log("## 2. Pump and dump (family A)\n")
        first = C.block_number(self.base)
        self.play("attack_a")
        self.latency_table("Pump and dump", first)
        self.attempt_blocked_swap()

        if with_drip:
            self.log("## 3. Slow drip and reversal (family C)\n")
            self.resume()
            first = C.block_number(self.base)
            self.play("drip_c")
            self.latency_table("Slow drip", first)
            self.attempt_blocked_swap()
        self.parity()
        self.stop.set()
        path = os.path.join(C.ROOT, "docs", "RUN.md")
        with open(path, "w") as fh:
            fh.write("\n".join(self.report) + "\n")
        print(f"\nwrote {os.path.relpath(path, C.ROOT)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and expected scores, touch no network")
    ap.add_argument("--no-drip", action="store_true", help="skip the second (family C) attack")
    args = ap.parse_args()
    if args.dry_run:
        dry_run()
    else:
        Demo().run(with_drip=not args.no_drip)


if __name__ == "__main__":
    main()
