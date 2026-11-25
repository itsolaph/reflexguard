#!/usr/bin/env bash
# Deploy ReflexGuard: tokens and pool on Base Sepolia, Reactive Contract on Lasna.
#
#   export PRIVATE_KEY=0x...            # funded on both networks; read by the forge scripts, never printed
#   export BASE_SEPOLIA_RPC=https://...
#   export LASNA_RPC=https://lasna-rpc.rnk.dev/
#   optional: RC_FUND (wei, default 0.1 ether), POOL_FUND (wei, default 0.02 ether)
#   scripts/deploy.sh
#
# The pool needs the RC address (immutable callback sender) and the RC needs the pool address, so the order
# is: tokens -> predict the pool's CREATE address -> RC on Lasna -> pool on Base Sepolia (checked against
# the prediction). Do not send other transactions from the deployer on Base Sepolia while this runs.
set -euo pipefail

: "${PRIVATE_KEY:?set PRIVATE_KEY}"
: "${BASE_SEPOLIA_RPC:?set BASE_SEPOLIA_RPC}"
: "${LASNA_RPC:?set LASNA_RPC}"
cd "$(dirname "$0")/.."

field() { grep -oE "^[[:space:]]*$1=0x[0-9a-fA-F]{40}" <<<"$2" | tail -n1 | cut -d= -f2; }

echo "== 1/3 tokens on Base Sepolia"
out=$(forge script script/DeployTokens.s.sol --rpc-url "$BASE_SEPOLIA_RPC" --broadcast)
DEPLOYER=$(field DEPLOYER "$out"); TOKEN0=$(field TOKEN0 "$out"); TOKEN1=$(field TOKEN1 "$out")
echo "deployer $DEPLOYER token0 $TOKEN0 token1 $TOKEN1"

nonce=$(cast nonce "$DEPLOYER" --rpc-url "$BASE_SEPOLIA_RPC")
POOL=$(cast compute-address "$DEPLOYER" --nonce "$nonce" | grep -oE '0x[0-9a-fA-F]{40}')
echo "predicted pool address $POOL (deployer nonce $nonce)"

echo "== 2/3 reactive contract on Lasna"
out=$(POOL=$POOL forge script script/DeployReactive.s.sol --rpc-url "$LASNA_RPC" --broadcast --legacy)
RC=$(field RC "$out")
echo "rc $RC"

echo "== 3/3 pool on Base Sepolia"
out=$(TOKEN0=$TOKEN0 TOKEN1=$TOKEN1 RC=$RC POOL=$POOL forge script script/DeployPool.s.sol --rpc-url "$BASE_SEPOLIA_RPC" --broadcast)
[ "$(field POOL "$out")" = "$POOL" ] || { echo "pool address mismatch" >&2; exit 1; }

cat > deployments.json <<EOF
{
  "deployer": "$DEPLOYER",
  "token0": "$TOKEN0",
  "token1": "$TOKEN1",
  "pool": "$POOL",
  "rc": "$RC"
}
EOF
echo "wrote deployments.json"
