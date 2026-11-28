#!/usr/bin/env bash
set -euo pipefail

mode="$1"
work="$(cd "$2" && pwd)"
source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
research="$(dirname "$source_dir")"
devnet="$work/devnet"
export ZENO_DEVNET_SRC="$devnet/.sources"
export DEVNET_BIND_ADDRESS=127.0.0.1
node="$ZENO_DEVNET_SRC/zenon-network/go-zenon"
executor="$ZENO_DEVNET_SRC/zeno/zeno-executor"
devnet_revision=cf1c9c0e0f0ca3b257bdbd019c5589c2c27f748f
node_revision=2835c436b197fece5c76adf4ba8dc1decca2e3ea
guest_key=0x00e4e8f52c5c085d833abcfacbbf0270e9832cd2af090ae12f7659c08270ffa4
recorded_key="$(jq -er '.guestKey' "$research/zeno-settlement-proof/groth16-fixture.json")"
nodes=(pillar pillar2 pillar3 observer rpc)
umask 077

case "$mode" in
  capture|capture-release|capture-native-release) capture=true ;;
  preflight|submit|submit-native|preflight-release|resume-release|resume-native-release) capture=false ;;
  *) exit 2 ;;
esac
test -n "${GITHUB_ACTIONS:-}" && test "$(uname -s)" = Linux
test "$(df -PB1 "$work" | awk 'NR == 2 { print $4 }')" -ge "$((21 * 1024 * 1024 * 1024))"
compose=(docker compose -p zeno-devnet -f "$node/docker-compose.yml")

devnet_command() (
  local limit="$1"
  shift
  cd "$devnet"
  timeout "$limit" npm exec --offline -- tsx flows/validity-submit.ts "$@"
)

checkpoint_command() (
  cd "$(dirname "$research")"
  timeout 10m python3 -B -m vault_evidence.native_checkpoint "$work" "$@"
)

stop_owned_devnet() {
  local result=$?
  trap - EXIT
  if [[ "$capture" = true ]]; then
    devnet_command 90s stop-devnet || result=1
  else
    timeout 90s "${compose[@]}" down --timeout 45 || result=1
  fi
  exit "$result"
}

if [[ "$mode" = preflight-release || "$mode" = resume-release || "$mode" = resume-native-release ]]; then
  test "$(git -C "$devnet" rev-parse HEAD)" = "$devnet_revision"
  test "$(git -C "$node" rev-parse HEAD)" = "$node_revision"
  test -s "$work/pending.json"
  if [[ "$mode" = resume-native-release ]]; then
    cmp "$research/zeno-native-checkpoint/native_checkpoint_devnet.go" "$node/rpc/api/native_checkpoint_devnet.go"
  fi
  trap stop_owned_devnet EXIT
  timeout 4m "${compose[@]}" up -d --no-build --pull never "${nodes[@]}"
  timeout 10m python3 -B "$research/zeno-withdrawal/bootstrap.py"
  if [[ "$mode" = preflight-release ]]; then
    devnet_command 5m preflight "$work"
  else
    devnet_command 20m submit-release "$work"
  fi
  if [[ "$mode" = resume-native-release ]]; then
    checkpoint_command --bitcoind "$work/bitcoind"
  fi
  exit
fi

pinned_checkout() {
  local destination="$1" url="$2" revision="$3"
  git init -q "$destination"
  timeout 120s git -C "$destination" fetch -q --depth 1 "$url" "$revision"
  git -C "$destination" checkout -q --detach FETCH_HEAD
  test "$(git -C "$destination" rev-parse HEAD)" = "$revision"
  printf 'VALIDITY_SOURCE %s %s\n' "$url" "$revision"
}

pinned_checkout "$devnet" https://github.com/Zeno-L2/zeno-devnet.git "$devnet_revision"
cp "$source_dir/submit.ts" "$devnet/flows/validity-submit.ts"
chmod 0644 "$devnet/config/gateway.json"
if [[ "$capture" = true ]]; then
  cp "$research/zeno-withdrawal/batch-proof.ts" "$devnet/flows/"
  cp "$research/zeno-contract-authorization/capture.ts" "$devnet/flows/vault-authorizer.ts"
fi
(
  cd "$devnet"
  timeout 8m npm ci --no-audit --no-fund
  timeout 2m npm run typecheck
)

if [[ "$capture" = true ]]; then
  timeout 16m bash "$research/zeno-contract-authorization/build-authorizer.sh" "$devnet"
  test "$(git -C "$node" rev-parse HEAD)" = "$node_revision"
else
  pinned_checkout "$node" https://github.com/digitalSloth/go-zenon.git "$node_revision"
fi
python3 -B "$research/zeno-settlement-proof/prepare-node.py" "$node" "$recorded_key" "$guest_key"
if [[ "$mode" = submit-native || "$mode" = capture-native-release ]]; then
  cp "$research/zeno-native-checkpoint/native_checkpoint_devnet.go" "$node/rpc/api/"
fi
(
  cd "$node"
  timeout 8m go mod tidy
)

if [[ "$capture" = true ]]; then
  command="$executor/cmd/vault-validity-witness"
  mkdir "$command"
  cp "$research/zeno-batch-proof/batch-witness.go" "$research/zeno-validity-statement/proposal.go" "$source_dir/witness.go" "$command/"
  (
    cd "$executor"
    timeout 8m go build -mod=mod -trimpath -o "$work/validity-witness" ./cmd/vault-validity-witness
  )
fi
trap stop_owned_devnet EXIT

timeout 12m "${compose[@]}" build pillar
if [[ "$capture" = true ]]; then
  devnet_command 18m build-executor
else
  timeout 2m "${compose[@]}" create --no-build "${nodes[@]}"
  sudo python3 -B "$source_dir/checkpoint.py" restore "$work/checkpoint.tar"
fi
timeout 4m "${compose[@]}" up -d --no-build --pull never "${nodes[@]}"
timeout 10m python3 -B "$research/zeno-withdrawal/bootstrap.py"

if [[ "$capture" = true ]]; then
  (
    cd "$devnet"
    timeout 18m npm exec --offline -- tsx src/cli.ts configure
  )
  devnet_command 4m start-executor
  ready=0
  for _ in $(seq 1 90); do
    result="$(curl -fsS --max-time 4 --max-filesize 16384 -H 'Content-Type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"zeno_getHead","params":[1]}' http://127.0.0.1:35999 || true)"
    if jq -e '.result.status == "OK"' >/dev/null <<<"$result"; then
      ready=1
      break
    fi
    sleep 2
  done
  test "$ready" = 1
  (
    cd "$devnet"
    args=("$work/vault_authorizer" "$work/pending.json")
    if [[ "$mode" = capture-release || "$mode" = capture-native-release ]]; then args+=("$work/target.hex"); fi
    timeout 24m npm exec --offline -- tsx flows/vault-authorizer.ts capture-pending "${args[@]}"
  )
  if [[ "$mode" = capture-native-release ]]; then
    checkpoint_command --check-genesis
  fi
  timeout 120s "$work/validity-witness" "$work"
  timeout 4m "${compose[@]}" stop --timeout 45 "${nodes[@]}"
  if [[ "$mode" = capture ]]; then
    sudo python3 -B "$source_dir/checkpoint.py" save "$work/checkpoint.tar"
    sudo chown --reference="$work" "$work/checkpoint.tar"
    chmod 0600 "$work/checkpoint.tar"
  fi
elif [[ "$mode" = submit-native ]]; then
  devnet_command 20m submit-release "$work"
  checkpoint_command --check-views
else
  devnet_command 18m "$mode" "$work"
fi
