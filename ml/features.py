"""Integer reference implementation of the on-chain ReflexGuard pipeline.

Everything in this module is integer arithmetic and mirrors
src/reactive/ReflexGuardRC.sol operation for operation: the same truncation
(`tdiv` for signed division, `>>` for flooring shifts), the same clipping and
the same lookup tables. The only floating point is the one-off generation of
the two lookup tables (DECAY, SIGMOID), whose values are copied into
src/reactive/ReflexTables.sol by ml/gen_tables.py.

See docs/SPEC.md for the written specification.
"""
import math

Q16 = 1 << 16
MAX_RES = 1 << 96          # reserves are clipped to this before any multiply
MAX_TS = (1 << 64) - 1     # timestamps are clipped to uint64
MAX_PRICE = (1 << 127) - 1 # Q32 price clip (state is stored in int128)
FEAT_MAX = 2048            # features are Q10 and clipped to [-2048, 2048]
FLOW_K, PRICE_K, RES_K = 5, 7, 10   # half-lives of 2**k seconds: 32 s, 128 s, 1024 s
N_IN, N_HID = 6, 8
HID_MAX = 16383            # hidden activations (Q10) are capped here
LOGIT_MAX = 8192           # logit (Q10) is clipped to +-8.0
N_WEIGHTS = N_HID * N_IN + N_HID + N_HID + 1   # 65
N_WORDS = 5                # 16 int16 weights per uint256 word

# 2**(-k/32) in Q16, k = 0..31
DECAY = [round(Q16 * 2 ** (-k / 32)) for k in range(32)]
# 10000 * sigmoid(x), x = -8.0 .. 8.0 in steps of 0.5 (33 points)
SIGMOID = [round(10000 / (1 + math.exp(-(i * 512 - LOGIT_MAX) / 1024))) for i in range(33)]


def clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def tdiv(a, b):
    """Signed division truncating toward zero (Solidity `/`); b > 0."""
    q = abs(a) // b
    return -q if a < 0 else q


def decay(dt, k):
    """Q16 factor 2**(-dt / 2**k), whole half-lives by shift, remainder by table."""
    whole = dt >> k
    frac = (dt & ((1 << k) - 1)) >> (k - 5)
    return DECAY[frac] >> whole


class State:
    """Rolling per-pool state held in the reactive contract's storage."""

    def __init__(self):
        self.ts = 0
        self.p_ema = 0     # Q32 price (token1 per token0), half-life 128 s
        self.r_ema = 0     # reserve0 EMA, half-life 1024 s
        self.flow = 0      # decayed signed flow, Q16 pool fraction (+ = token0 in)
        self.vol = 0       # decayed absolute volume, Q16 pool fraction
        self.rate = 0      # decayed swap count, Q16

    def reset(self):
        self.__init__()


def update(s, zero_for_one, amount_in, r0, r1, ts):
    """Advance the state with one swap and return the 6 Q10 features, or None if skipped."""
    if r0 == 0 or r1 == 0:
        return None
    ts = min(ts, MAX_TS)
    r0 = min(r0, MAX_RES)
    r1 = min(r1, MAX_RES)
    r_in = r0 if zero_for_one else r1
    rel = min(amount_in, r_in) * Q16 // r_in
    price = clip((r1 << 32) // r0, 1, MAX_PRICE)
    sign = 1 if zero_for_one else -1

    if s.p_ema != 0:      # a real price EMA is always >= 1, so 0 means "no swap seen yet"
        dt = ts - s.ts if ts > s.ts else 0
        d_f, d_p, d_r = decay(dt, FLOW_K), decay(dt, PRICE_K), decay(dt, RES_K)
        flow = (s.flow * d_f) >> 16
        vol = (s.vol * d_f) >> 16
        rate = (s.rate * d_f) >> 16
        p_old, r_old = s.p_ema, s.r_ema
    else:
        d_p = d_r = Q16
        flow = vol = rate = 0
        p_old, r_old = price, r0

    flow_new = flow + sign * rel
    vol_new = vol + rel
    rate_new = rate + Q16

    feats = [
        min(rel >> 3, FEAT_MAX),
        clip((-sign * flow) >> 3, -FEAT_MAX, FEAT_MAX),
        clip(tdiv((r0 - r_old) * 8192, max(r_old, 1)), -FEAT_MAX, FEAT_MAX),
        min(rate_new >> 10, FEAT_MAX),
        min(vol_new >> 4, FEAT_MAX),
        clip(tdiv((price - p_old) * 8192, max(p_old, 1)), -FEAT_MAX, FEAT_MAX),
    ]

    s.p_ema = p_old + (((price - p_old) * (Q16 - d_p)) >> 16)
    s.r_ema = r_old + (((r0 - r_old) * (Q16 - d_r)) >> 16)
    s.flow, s.vol, s.rate = flow_new, vol_new, rate_new
    s.ts = ts
    assert max(abs(flow_new), vol_new, rate_new) < 1 << 63   # contract stores these as int64
    return feats


def pack_features(feats):
    """Six int16 features into one uint96, feature 0 in the lowest 16 bits."""
    out = 0
    for i, f in enumerate(feats):
        out |= (f & 0xFFFF) << (16 * i)
    return out


def sigmoid_lut(logit):
    z = clip(logit, -LOGIT_MAX, LOGIT_MAX) + LOGIT_MAX
    i = z >> 9
    if i >= 32:
        return SIGMOID[32]
    return SIGMOID[i] + (((SIGMOID[i + 1] - SIGMOID[i]) * (z & 511)) >> 9)


def split_weights(w):
    """Flat list of 65 ints -> (W1[8][6], b1[8], W2[8], b2)."""
    w1 = [w[j * N_IN:(j + 1) * N_IN] for j in range(N_HID)]
    o = N_HID * N_IN
    return w1, w[o:o + N_HID], w[o + N_HID:o + 2 * N_HID], w[o + 2 * N_HID]


def mlp(feats, w):
    """6 -> 8 ReLU -> 1 in Q8 weights / Q10 activations; returns score 0..10000."""
    w1, b1, w2, b2 = split_weights(w)
    acc2 = 0
    for j in range(N_HID):
        acc = sum(w1[j][i] * feats[i] for i in range(N_IN))
        h = clip((acc >> 8) + b1[j], 0, HID_MAX)
        acc2 += w2[j] * h
    return sigmoid_lut((acc2 >> 8) + b2)


def pack_weights(w):
    """65 int16 weights -> 5 uint256 words, weight n at bits 16*(n%16) of word n//16."""
    assert len(w) == N_WEIGHTS and all(-32768 <= v <= 32767 for v in w)
    words = [0] * N_WORDS
    for n, v in enumerate(w):
        words[n // 16] |= (v & 0xFFFF) << (16 * (n % 16))
    return words


def score_sequence(events, w):
    """Run a list of (zero_for_one, amount_in, amount_out, r0, r1, ts) through the pipeline.

    Returns a list of (features, score), one per event (None entries for skipped events).
    """
    s = State()
    out = []
    for z2o, amt_in, _amt_out, r0, r1, ts in events:
        f = update(s, z2o, amt_in, r0, r1, ts)
        out.append(None if f is None else (f, mlp(f, w)))
    return out
