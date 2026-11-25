"""Evaluation helpers: per-swap and per-episode metrics on scored scenarios."""
import numpy as np


def auc(scores, labels):
    """ROC AUC by the rank-sum statistic (ties get average rank)."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels)
    pos = labels == 1
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(scores, kind="mergesort")
    sorted_scores = scores[order]
    ranks = np.empty(len(scores))
    i = 0
    while i < len(scores):
        j = i
        while j + 1 < len(scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def per_swap(scores, labels, thr):
    """Confusion counts and rates at a threshold."""
    s, y = np.asarray(scores), np.asarray(labels)
    flag = s >= thr
    tp, fp = int((flag & (y == 1)).sum()), int((flag & (y == 0)).sum())
    fn, tn = int((~flag & (y == 1)).sum()), int((~flag & (y == 0)).sum())
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else float("nan"),
            "recall": tp / (tp + fn) if tp + fn else float("nan"),
            "fpr": fp / (fp + tn) if fp + tn else float("nan"),
            "auc": auc(s, y)}


def episodes(scenarios, scores_by_id, thr):
    """Per-scenario outcomes. A scenario is `halted` if any swap reaches the threshold.

    For attacks, `caught` means some swap labelled 1 reached the threshold; `early` means a swap before the
    first reversal leg did (the pool would have been halted before the attacker started unwinding).
    """
    out = []
    for sc in scenarios:
        s = np.asarray(scores_by_id[sc["id"]])
        y = np.asarray(sc["labels"])
        flag = s >= thr
        rec = {"id": sc["id"], "kind": sc["kind"], "family": sc["family"], "halted": bool(flag.any())}
        if sc["family"] != "benign":
            first_rev = min(sc["reversal"])
            rec["caught"] = bool((flag & (y == 1)).any())
            rec["early"] = bool(flag[:first_rev].any())
            rec["reversal_hit"] = float(flag[sc["reversal"]].mean())
        out.append(rec)
    return out


def summarise(eps):
    """Aggregate episode records: false-halt rate on benign, recall by attack family."""
    res = {}
    benign = [e for e in eps if e["family"] == "benign"]
    if benign:
        res["benign_scenarios"] = len(benign)
        res["benign_false_halt_rate"] = sum(e["halted"] for e in benign) / len(benign)
        res["benign_by_kind"] = {k: (sum(e["halted"] for e in benign if e["kind"] == k),
                                     sum(1 for e in benign if e["kind"] == k))
                                 for k in sorted({e["kind"] for e in benign})}
    for fam in "ABC":
        a = [e for e in eps if e["family"] == fam]
        if a:
            res[fam] = {"n": len(a), "caught": sum(e["caught"] for e in a) / len(a),
                        "early": sum(e["early"] for e in a) / len(a),
                        "reversal_leg_recall": float(np.mean([e["reversal_hit"] for e in a]))}
    return res
