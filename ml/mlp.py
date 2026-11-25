"""Float 6 -> 8 -> 1 MLP with hand-written backprop (numpy only) and quantisation to the on-chain format."""
import numpy as np

import features as F


class FloatMLP:
    """Inputs are the integer features divided by 1024. Hidden ReLU is capped at 16 like the integer model."""

    def __init__(self, rng):
        self.w1 = rng.normal(0, 0.8, (F.N_HID, F.N_IN))
        self.b1 = np.zeros(F.N_HID)
        self.w2 = rng.normal(0, 0.5, F.N_HID)
        self.b2 = np.array(-2.0)

    def forward(self, x):
        pre = x @ self.w1.T + self.b1
        h = np.clip(pre, 0, HID_CAP)
        return pre, h, h @ self.w2 + self.b2

    def predict(self, x):
        """Probability, same cap on the logit as the integer model (+-8)."""
        z = np.clip(self.forward(x)[2], -8, 8)
        return 1 / (1 + np.exp(-z))

    def fit(self, x, y, rng, epochs=200, batch=512, lr=1e-2, pos_weight=3.0, l2=1e-4, verbose=False):
        params = [self.w1, self.b1, self.w2, self.b2]
        m = [np.zeros_like(p) for p in params]
        v = [np.zeros_like(p) for p in params]
        t = 0
        n = len(y)
        for epoch in range(epochs):
            order = rng.permutation(n)
            step_lr = lr * (0.5 * (1 + np.cos(np.pi * epoch / epochs)) * 0.95 + 0.05)
            total = 0.0
            for i in range(0, n, batch):
                idx = order[i:i + batch]
                xb, yb = x[idx], y[idx]
                pre, h, z = self.forward(xb)
                p = 1 / (1 + np.exp(-z))
                sw = np.where(yb > 0.5, pos_weight, 1.0)
                total += float((sw * -(yb * np.log(p + 1e-9) + (1 - yb) * np.log(1 - p + 1e-9))).sum())
                dz = sw * (p - yb) / len(idx)
                dh = np.outer(dz, self.w2) * ((pre > 0) & (pre < HID_CAP))
                grads = [dh.T @ xb + l2 * self.w1, dh.sum(0), h.T @ dz + l2 * self.w2, np.array(dz.sum())]
                t += 1
                for k, (p_, g) in enumerate(zip(params, grads)):
                    m[k] = 0.9 * m[k] + 0.1 * g
                    v[k] = 0.999 * v[k] + 0.001 * g * g
                    p_ -= step_lr * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v[k] / (1 - 0.999 ** t)) + 1e-8)
            if verbose and epoch % 10 == 0:
                print(f"  epoch {epoch:3d} loss {total / n:.4f}")

    def quantise(self):
        """Flat list of 65 ints: W1 (Q8, row-major), b1 (Q10), W2 (Q8), b2 (Q10)."""
        def q(a, scale):
            return [int(v) for v in np.clip(np.rint(np.ravel(a) * scale), -32768, 32767)]
        return q(self.w1, 256) + q(self.b1, 1024) + q(self.w2, 256) + q(self.b2, 1024)


HID_CAP = 16.0
