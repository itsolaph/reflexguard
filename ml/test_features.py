"""Sanity tests for the integer reference.  Run: .venv/bin/python -m unittest discover -s ml"""
import json
import os
import unittest

import features as F
import sim

HERE = os.path.dirname(os.path.abspath(__file__))


class TestTables(unittest.TestCase):
    def test_decay_table_is_monotone_and_starts_at_one(self):
        self.assertEqual(F.DECAY[0], 65536)
        self.assertTrue(all(a > b for a, b in zip(F.DECAY, F.DECAY[1:])))

    def test_decay_halves_per_half_life(self):
        for k in (5, 7, 10):
            self.assertEqual(F.decay(1 << k, k), 32768)
            self.assertEqual(F.decay(3 << k, k), 8192)
            self.assertEqual(F.decay(0, k), 65536)

    def test_sigmoid_table_is_monotone_and_symmetric(self):
        self.assertTrue(all(a < b for a, b in zip(F.SIGMOID, F.SIGMOID[1:])))
        self.assertEqual(F.SIGMOID[16], 5000)
        for i in range(33):
            self.assertEqual(F.SIGMOID[i] + F.SIGMOID[32 - i], 10000)

    def test_sigmoid_lut_endpoints_and_clipping(self):
        self.assertEqual(F.sigmoid_lut(0), 5000)
        self.assertEqual(F.sigmoid_lut(10 ** 9), F.SIGMOID[32])
        self.assertEqual(F.sigmoid_lut(-10 ** 9), F.SIGMOID[0])


class TestArithmetic(unittest.TestCase):
    def test_tdiv_truncates_toward_zero(self):
        self.assertEqual(F.tdiv(-7, 2), -3)
        self.assertEqual(F.tdiv(7, 2), 3)
        self.assertEqual(F.tdiv(-1, 5), 0)

    def test_pack_weights_round_trip(self):
        w = [(-1) ** n * (n * 37 + 1) for n in range(F.N_WEIGHTS)]
        words = F.pack_weights(w)
        back = []
        for n in range(F.N_WEIGHTS):
            v = (words[n // 16] >> (16 * (n % 16))) & 0xFFFF
            back.append(v - 0x10000 if v & 0x8000 else v)
        self.assertEqual(back, w)

    def test_first_swap_has_no_history(self):
        s = F.State()
        f = F.update(s, True, 10 ** 20, 10 ** 24, 10 ** 24, 1000)
        self.assertEqual(f[1], 0)   # nothing to reverse
        self.assertEqual(f[2], 0)   # reserve EMA starts at the current value
        self.assertEqual(f[5], 0)   # price EMA starts at the current value

    def test_reversal_feature_is_positive_after_opposite_swap(self):
        s = F.State()
        F.update(s, True, 10 ** 23, 10 ** 24, 10 ** 24, 1000)
        f = F.update(s, False, 10 ** 23, 10 ** 24, 10 ** 24, 1002)
        self.assertGreater(f[1], 0)

    def test_zero_reserve_is_skipped_and_leaves_state(self):
        s = F.State()
        self.assertIsNone(F.update(s, True, 1, 0, 5, 10))
        self.assertEqual(s.p_ema, 0)


class TestShippedArtifacts(unittest.TestCase):
    def test_vectors_match_reference(self):
        with open(os.path.join(HERE, "weights.json")) as fh:
            w = json.load(fh)["weights"]
        with open(os.path.join(HERE, "vectors.json")) as fh:
            v = json.load(fh)
        self.assertGreaterEqual(len(v["seqName"]), 40)
        for s in range(len(v["seqName"])):
            lo, hi = v["seqStart"][s], v["seqStart"][s + 1]
            events = [(v["zeroForOne"][i] == 1, int(v["amountIn"][i]), int(v["amountOut"][i]), int(v["reserve0"][i]),
                       int(v["reserve1"][i]), int(v["timestamp"][i])) for i in range(lo, hi)]
            for i, res in zip(range(lo, hi), F.score_sequence(events, w)):
                self.assertEqual(v["expectedScore"][i], 0 if res is None else res[1])

    def test_simulated_pool_conserves_invariant_growth(self):
        pool = sim.Pool(10 ** 24, 10 ** 24)
        k0 = pool.r0 * pool.r1
        pool.swap(True, 10 ** 22)
        self.assertGreaterEqual(pool.r0 * pool.r1, k0)   # fee makes k grow


if __name__ == "__main__":
    unittest.main()
