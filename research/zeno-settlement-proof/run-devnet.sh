#!/usr/bin/env bash
set -euo pipefail

work="$1"
guest_key="$2"
fixture="$3"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
node="$work/node"
devnet="$work/devnet"
export DEVNET_BIND_ADDRESS=127.0.0.1
umask 077

pinned_checkout() {
  local dest="$1" url="$2" rev="$3"
  git init -q "$dest"
  timeout 120s git -C "$dest" fetch -q --depth 1 "$url" "$rev"
  git -C "$dest" checkout --quiet --detach FETCH_HEAD
  test "$(git -C "$dest" rev-parse HEAD)" = "$rev"
  printf 'SETTLEMENT_DEVNET_SOURCE %s %s\n' "$url" "$rev"
}

free_bytes="$(df -PB1 "$work" | awk 'NR == 2 {print $4}')"
test "$free_bytes" -ge "$((18 * 1024 * 1024 * 1024))"
pinned_checkout "$node" https://github.com/digitalSloth/go-zenon.git 2835c436b197fece5c76adf4ba8dc1decca2e3ea
pinned_checkout "$devnet" https://github.com/Zeno-L2/zeno-devnet.git cf1c9c0e0f0ca3b257bdbd019c5589c2c27f748f
python3 -B "$root/research/zeno-settlement-proof/prepare-node.py" "$node" "$guest_key"
cp "$root/research/zeno-settlement-proof/verify-on-devnet.ts" "$devnet/flows/"
(
  cd "$node"
  timeout 8m go mod tidy
)
(
  cd "$devnet"
  timeout 8m npm ci --no-audit --no-fund
  timeout 2m npm run typecheck
)

compose=(docker compose -p zeno-devnet -f "$node/docker-compose.yml")
stop_owned_nodes() {
  local status=$?
  trap - EXIT
  timeout 75s "${compose[@]}" down || status=1
  exit "$status"
}
trap stop_owned_nodes EXIT
timeout 12m "${compose[@]}" build pillar
timeout 3m "${compose[@]}" up -d --no-build --pull never pillar pillar2 pillar3 observer rpc
timeout 10m python3 -B "$root/research/zeno-withdrawal/bootstrap.py"
(
  cd "$devnet"
  timeout 14m npm exec --offline -- tsx flows/verify-on-devnet.ts \
    "$fixture" "$guest_key" "$work/settlement-evidence.json"
)
