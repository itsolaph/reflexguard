"""Shared helpers for demo.py and watch.py: thin wrappers around the `cast` CLI. Standard library only."""
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "ml"))
import features as F  # noqa: E402  (stdlib-only integer reference, reused to cross-check on-chain scores)

SCORED_SIG = "Scored(address,uint256,uint256,uint256,uint256)"
HALTED_SIG = "Halted(uint256,uint256,uint256)"
SWAP_SIG = "Swap(address,bool,uint256,uint256,uint256,uint256,uint256)"
RESUMED_SIG = "Resumed(uint256)"
REARMED_SIG = "Rearmed(address,uint256)"


class CastError(RuntimeError):
    pass


def env(name):
    value = os.environ.get(name)
    if not value:
        sys.exit(f"missing environment variable {name}")
    return value


def redact(args):
    out, skip = [], False
    for a in args:
        out.append("<redacted>" if skip else a)
        skip = a == "--private-key"
    return out


def cast(*args, input_text=None):
    """Run cast and return stdout; raise CastError with stderr on failure. The key never appears in errors."""
    proc = subprocess.run(["cast", *map(str, args)], capture_output=True, text=True, input=input_text)
    if proc.returncode != 0:
        raise CastError(f"cast {' '.join(redact([str(a) for a in args]))}\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def to_int(x):
    """Integer from a number or a hex/decimal string; cast sometimes appends an annotation like `[1.7e9]`."""
    return int(x.split()[0], 0) if isinstance(x, str) else int(x)


def block_number(rpc):
    return to_int(cast("block-number", "--rpc-url", rpc))


def block_timestamp(rpc, number):
    return to_int(cast("block", number, "--rpc-url", rpc, "--field", "timestamp"))


def send(rpc, key, to, sig, *args):
    """Send a transaction and wait for the receipt. Returns the parsed receipt."""
    out = cast("send", "--rpc-url", rpc, "--private-key", key, "--json", to, sig, *args)
    receipt = json.loads(out)
    if to_int(receipt.get("status", 1)) != 1:
        raise CastError(f"transaction {receipt.get('transactionHash')} reverted")
    return receipt


def call(rpc, to, sig, *args, sender=None):
    extra = ["--from", sender] if sender else []
    return cast("call", "--rpc-url", rpc, *extra, to, sig, *args)


def logs(rpc, address, signature, from_block, chunk=180):
    """Decoded-by-hand logs, chunked to stay within public RPC eth_getLogs block-range limits."""
    tip = block_number(rpc)
    if from_block > tip:
        return []
    raw = []
    cur = from_block
    while cur <= tip:
        end = min(cur + chunk - 1, tip)
        out = cast("logs", "--rpc-url", rpc, "--address", address, "--from-block", cur, "--to-block", end,
                   "--json", signature)
        raw.extend(json.loads(out or "[]"))
        cur = end + 1
    rows = []
    for log in raw:
        data = log["data"][2:]
        rows.append({
            "block": to_int(log["blockNumber"]),
            "tx": log["transactionHash"],
            "index": to_int(log.get("logIndex", 0)),
            "topics": [to_int(t) for t in log["topics"]],
            "words": [int(data[i:i + 64], 16) for i in range(0, len(data), 64)],
        })
    return sorted(rows, key=lambda r: (r["block"], r["index"]))


def unpack_features(packed):
    """Six int16 Q10 features from the packed uint96 in the Scored event."""
    out = []
    for i in range(6):
        v = (packed >> (16 * i)) & 0xFFFF
        out.append(v - 0x10000 if v & 0x8000 else v)
    return out


def load_deployments():
    path = os.path.join(ROOT, "deployments.json")
    if not os.path.exists(path):
        sys.exit("deployments.json not found: run scripts/deploy.sh first")
    with open(path) as fh:
        return json.load(fh)


def load_weights():
    with open(os.path.join(ROOT, "ml", "weights.json")) as fh:
        return json.load(fh)
