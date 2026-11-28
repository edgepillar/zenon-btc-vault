#!/usr/bin/env bash
set -euo pipefail

devnet="$1"
work_dir="$(dirname "$devnet")"
sdk="$work_dir/authorizer-sdk"
src="$devnet/.sources"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="${ZENO_MODE:-capture}"
test "$mode" = capture || test "$mode" = replay

# Keep the contract build overlay outside the checkout managed by devnet.sh.
while read -r repo_path url revision; do
  case "$repo_path" in
    zeno/zeno-contract-sdk-rs)
      [ "$mode" = capture ] || continue
      destination="$sdk"
      ;;
    zeno/zeno-executor|zenon-network/go-zenon) destination="$src/$repo_path" ;;
    *) continue ;;
  esac
  git init --quiet "$destination"
  timeout 120s git -C "$destination" fetch --quiet --depth 1 "$url" "$revision"
  git -C "$destination" checkout --quiet --detach FETCH_HEAD
  test "$(git -C "$destination" rev-parse HEAD)" = "$revision"
done < "$devnet/sources.txt"

executor="$src/zeno/zeno-executor"
replay="$executor/cmd/vault-execution-replay"
mkdir "$replay"
cp "$here/../zeno-execution-replay/replay.go" "$replay/"
cp "$here/../zeno-batch-proof/batch-witness.go" "$replay/"
(
  cd "$executor"
  go version
  GOWORK=off go build -mod=readonly -trimpath -o "$work_dir/verify-execution" ./cmd/vault-execution-replay
)
# This source overlay belongs to the build. Leave the pinned devnet checkout clean.
rm "$replay/replay.go" "$replay/batch-witness.go"
rmdir "$replay"
printf 'Execution verifier SHA-256 '
sha256sum < "$work_dir/verify-execution" | cut -d ' ' -f 1
if [ "$mode" = replay ]; then
  exit 0
fi

(
  cd "$executor"
  GOWORK=off go build -mod=readonly -trimpath -o "$work_dir/zeno-exec" ./cmd/zeno-exec
)

cd "$sdk"
cargo fetch --locked
example="$sdk/examples/vault_authorizer"
cp -R "$here/contract" "$example"
(
  cd "$devnet"
  npm exec --offline -- tsx flows/vault-authorizer.ts caller "$example/src/caller.bin"
)
# Only the local workspace member may change the pinned dependency lock.
cargo update --workspace --offline
python3 -B - <<'PY'
from pathlib import Path
import subprocess
import tomllib

old = tomllib.loads(subprocess.check_output(["git", "show", "HEAD:Cargo.lock"], text=True))
new = tomllib.loads(Path("Cargo.lock").read_text(encoding="utf-8"))
addition = {"name": "vault_authorizer", "version": "0.1.0", "dependencies": ["zeno-contract"]}
new["package"].remove(addition)
if new != old:
    raise SystemExit("Only the local authorizer package may change the SDK lock")
PY
cargo build --locked --release --package cargo-zeno
ZENO_EXEC="$work_dir/zeno-exec" "$sdk/target/release/cargo-zeno" build
cp "$example/artifacts/vault_authorizer.wasm" "$work_dir/vault_authorizer.wasm"
cp "$example/artifacts/vault_authorizer.interface.json" "$work_dir/vault_authorizer.interface.json"
printf 'Authorizer WASM SHA-256 '
sha256sum < "$work_dir/vault_authorizer.wasm" | cut -d ' ' -f 1
