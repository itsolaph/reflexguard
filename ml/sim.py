"""Synthetic order flow for a constant-product pool: benign regimes and three attack families.

Every scenario is a fresh pool plus a list of swap events with the same integer pool arithmetic as
src/origin/GuardedPool.sol (0.3% fee, floor division), so the events can be replayed through the RC.

A scenario is a dict:
    kind      benign regime or attack family name
    family    "benign", "A", "B" or "C"
    events    list of (zero_for_one, amount_in, amount_out, reserve0, reserve1, timestamp)
    labels    list of 0/1, one per event; 1 marks the swaps that unwind an attacker's position
    reversal  indices of the swaps that unwind the attacker's position (empty for benign)

All randomness comes from the `random.Random` passed in, so a seed fixes the dataset.
"""
import math
import random

E18 = 10 ** 18
FEE_N, FEE_D = 997, 1000


class Pool:
    def __init__(self, r0, r1):
        self.r0, self.r1 = r0, r1

    def quote(self, zero_for_one, amount_in):
        r_in, r_out = (self.r0, self.r1) if zero_for_one else (self.r1, self.r0)
        fee_in = amount_in * FEE_N
        return fee_in * r_out // (r_in * FEE_D + fee_in)

    def swap(self, zero_for_one, amount_in):
        out = self.quote(zero_for_one, amount_in)
        if zero_for_one:
            self.r0 += amount_in
            self.r1 -= out
        else:
            self.r1 += amount_in
            self.r0 -= out
        return out

    @property
    def price(self):
        return self.r1 / self.r0


class Sim:
    """One scenario under construction: a pool, a clock and the recorded events."""

    def __init__(self, rng, r0=None, r1=None, t0=None):
        self.rng = rng
        if r0 is None:
            scale = math.exp(rng.uniform(math.log(1e4), math.log(1e7)))
            price = math.exp(rng.uniform(math.log(0.3), math.log(3.0)))
            r0, r1 = int(scale * E18), int(scale * price * E18)
        self.pool = Pool(r0, r1)
        self.t = float(t0 if t0 is not None else rng.randint(1_700_000_000, 1_800_000_000))
        self.events, self.labels, self.reversal = [], [], []

    def swap(self, zero_for_one, amount_in, gap, label=0, reverses=False):
        """Advance the clock by `gap` seconds and execute a swap; returns the output amount."""
        self.t += gap
        amount_in = max(int(amount_in), 1)
        out = self.pool.swap(zero_for_one, amount_in)
        if reverses:
            self.reversal.append(len(self.events))
        self.events.append((zero_for_one, amount_in, out, self.pool.r0, self.pool.r1, int(self.t)))
        self.labels.append(label)
        return out

    def frac_swap(self, zero_for_one, frac, gap, **kw):
        r_in = self.pool.r0 if zero_for_one else self.pool.r1
        return self.swap(zero_for_one, r_in * frac, gap, **kw)

    def done(self, kind, family):
        return {"kind": kind, "family": family, "events": self.events, "labels": self.labels,
                "reversal": self.reversal}


def lognormal(rng, median, sigma, lo, hi):
    return min(max(median * math.exp(rng.gauss(0, sigma)), lo), hi)


def retail(sim, n, mean_gap, median=0.003, p_same=0.5):
    """Independent small trades with exponential gaps and mild direction persistence."""
    rng = sim.rng
    z2o = rng.random() < 0.5
    for _ in range(n):
        if rng.random() > p_same:
            z2o = not z2o
        sim.frac_swap(z2o, lognormal(rng, median, 0.8, 1e-4, 0.03), rng.expovariate(1 / mean_gap))


def background(sim, n=None):
    retail(sim, n if n is not None else sim.rng.randint(3, 14), math.exp(sim.rng.uniform(math.log(2), math.log(40))),
           median=sim.rng.uniform(0.001, 0.006))


# ---------------------------------------------------------------- benign regimes

def benign_retail(rng):
    sim = Sim(rng)
    retail(sim, rng.randint(25, 80), math.exp(rng.uniform(math.log(1.5), math.log(60))),
           median=rng.uniform(0.001, 0.008), p_same=rng.uniform(0.35, 0.65))
    return sim.done("retail", "benign")


def benign_whale(rng):
    """Retail flow with large one-way swaps. Some are later partly unwound or arbitraged back, slowly."""
    sim = Sim(rng)
    gap = math.exp(rng.uniform(math.log(2), math.log(40)))
    retail(sim, rng.randint(6, 20), gap)
    for _ in range(rng.randint(1, 3)):
        z2o = rng.random() < 0.5
        received = sim.frac_swap(z2o, rng.uniform(0.03, 0.22), rng.uniform(1, 30))
        retail(sim, rng.randint(2, 10), gap)
        if rng.random() < 0.3:     # unwound / arbitraged back only after a long delay
            sim.swap(not z2o, received * rng.uniform(0.3, 1.0), rng.uniform(25, 600))
        retail(sim, rng.randint(2, 8), gap)
    return sim.done("whale", "benign")


def benign_arb(rng):
    """Retail flow plus arbitrageurs that pull the pool toward a drifting external price."""
    sim = Sim(rng)
    p_ext = sim.pool.price
    sigma = rng.uniform(0.0003, 0.002)       # per sqrt(second)
    gap = math.exp(rng.uniform(math.log(2), math.log(30)))
    for _ in range(rng.randint(30, 90)):
        dt = rng.expovariate(1 / gap)
        p_ext *= math.exp(rng.gauss(0, sigma * math.sqrt(dt)))
        if rng.random() < 0.06:
            p_ext *= math.exp(rng.choice((-1, 1)) * rng.uniform(0.01, 0.04))
        z2o = rng.random() < 0.5
        sim.frac_swap(z2o, lognormal(rng, 0.003, 0.8, 1e-4, 0.03), dt)
        dev = sim.pool.price / p_ext - 1
        if abs(dev) > 0.006:
            k = sim.pool.r0 * sim.pool.r1
            target = p_ext * (1 + math.copysign(0.002, dev))
            if dev > 0:      # token0 too expensive: sell token0
                amount = math.sqrt(k / target) - sim.pool.r0
                sim.swap(True, amount * FEE_D / FEE_N, rng.uniform(1, 8))
            else:
                amount = math.sqrt(k * target) - sim.pool.r1
                sim.swap(False, amount * FEE_D / FEE_N, rng.uniform(1, 8))
    return sim.done("arb", "benign")


def benign_burst(rng):
    """Honest rushes: many mid-sized swaps in quick succession, direction persistent but not reversed."""
    sim = Sim(rng)
    retail(sim, rng.randint(4, 12), math.exp(rng.uniform(math.log(2), math.log(30))))
    for _ in range(rng.randint(1, 3)):
        p_same = rng.uniform(0.5, 0.9)
        median = rng.choice((0.003, 0.006, 0.012, 0.02))
        z2o = rng.random() < 0.5
        for _ in range(rng.randint(5, 25)):
            if rng.random() > p_same:
                z2o = not z2o
            sim.frac_swap(z2o, lognormal(rng, median, 0.6, 5e-4, 0.05), rng.uniform(0.3, 5))
        retail(sim, rng.randint(2, 8), math.exp(rng.uniform(math.log(3), math.log(40))))
    return sim.done("burst", "benign")


# ---------------------------------------------------------------- attack families

def _unwind(sim, z2o_attack, received, chunks, first_gap, gap_lo, gap_hi):
    """Sell back `received` tokens (the attack's output) in `chunks` pieces; the first after `first_gap`."""
    left = received
    for c in range(chunks):
        part = left if c == chunks - 1 else left // chunks
        sim.swap(not z2o_attack, part, first_gap if c == 0 else sim.rng.uniform(gap_lo, gap_hi), label=1, reverses=True)
        left -= part


def _tail(sim):
    retail(sim, sim.rng.randint(0, 2), 20)


def attack_a(rng):
    """Pump and dump: one big swap, then the same position unwound within seconds."""
    sim = Sim(rng)
    background(sim)
    z2o = rng.random() < 0.5
    received = sim.frac_swap(z2o, rng.uniform(0.03, 0.25), rng.uniform(1, 20))
    _unwind(sim, z2o, received, rng.choice((1, 1, 2, 3)), math.exp(rng.uniform(math.log(1), math.log(20))), 0, 3)
    _tail(sim)
    return sim.done("pump_dump", "A")


def attack_b(rng):
    """Sandwich: attacker front-run, victim swap, attacker back-run."""
    sim = Sim(rng)
    background(sim)
    z2o = rng.random() < 0.5
    received = sim.frac_swap(z2o, rng.uniform(0.03, 0.2), rng.uniform(1, 20))
    sim.frac_swap(z2o, rng.uniform(0.005, 0.06), rng.uniform(0, 3))
    _unwind(sim, z2o, received, rng.choice((1, 1, 2)), rng.uniform(0, 6), 0, 3)
    _tail(sim)
    return sim.done("sandwich", "B")


def attack_c(rng):
    """Slow drip: many mid-sized same-direction swaps in rapid succession, then one reversal."""
    sim = Sim(rng)
    background(sim)
    z2o = rng.random() < 0.5
    received = 0
    n = rng.randint(5, 12)
    for i in range(n):
        received += sim.frac_swap(z2o, rng.uniform(0.01, 0.04), rng.uniform(0.5, 5) if i else rng.uniform(1, 20))
    _unwind(sim, z2o, received, rng.choice((1, 1, 2, 3)), rng.uniform(1, 10), 1, 6)
    _tail(sim)
    return sim.done("drip", "C")


BENIGN = (benign_retail, benign_whale, benign_arb, benign_burst)
ATTACKS = {"A": attack_a, "B": attack_b, "C": attack_c}


def generate(seed, n_benign_each, n_attack_each):
    """Return a list of scenarios, grouped by generator, each tagged with a unique id."""
    rng = random.Random(seed)
    out = []
    for gen in BENIGN:
        out += [gen(rng) for _ in range(n_benign_each)]
    for fam in "ABC":
        out += [ATTACKS[fam](rng) for _ in range(n_attack_each)]
    for i, sc in enumerate(out):
        sc["id"] = i
    return out


# ---------------------------------------------------------------- demo plan

DEMO_LIQUIDITY = 100_000 * E18


def demo_plan(gap=3):
    """Fixed action list for the live demo: benign flow incl. a whale, a pump-and-dump, then a drip.

    Amounts are exact for a pool seeded with DEMO_LIQUIDITY of each token. `gap` seconds separate actions.
    Returns {"warmup": steps, "attack_a": steps, "drip_c": steps}; a step is (label, zero_for_one, amount_in).
    """
    pool = Pool(DEMO_LIQUIDITY, DEMO_LIQUIDITY)
    t = E18

    def do(plan, label, z2o, amount):
        pool.swap(z2o, amount)
        plan.append((label, z2o, amount))

    warmup, attack, drip = [], [], []
    for z2o, tokens in ((True, 200), (False, 150), (True, 300), (False, 250), (True, 100)):
        do(warmup, "retail", z2o, tokens * t)
    do(warmup, "whale", True, 6_000 * t)
    for z2o, tokens in ((False, 150), (True, 200), (False, 100)):
        do(warmup, "retail", z2o, tokens * t)

    out = pool.quote(False, 12_000 * t)
    do(attack, "pump", False, 12_000 * t)
    half = out // 2
    do(attack, "dump_1", True, half)
    do(attack, "dump_2", True, out - half)

    # a fresh guard state (after resume) sees the drip; the pool is rebalanced first by the plan itself
    received = 0
    for _ in range(7):
        received += pool.quote(True, 3_000 * t)
        do(drip, "drip", True, 3_000 * t)
    do(drip, "reverse", False, received)
    return {"warmup": warmup, "attack_a": attack, "drip_c": drip, "gap": gap}
