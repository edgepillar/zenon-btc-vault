#!/usr/bin/env bash
set -euo pipefail

work="$1"
source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
host_source="${2:-$source_dir/ziren/host.rs}"
proposal_source="${3:-}"
executor="$work/zeno/zeno-executor"
zenon="$work/zenon-network/go-zenon"
ziren="$work/ziren"

checkout() {
  local destination="$1" url="$2" revision="$3"
  git init --quiet "$destination"
  timeout 120s git -C "$destination" fetch --quiet --depth 1 "$url" "$revision"
  git -C "$destination" checkout --quiet --detach FETCH_HEAD
  test "$(git -C "$destination" rev-parse HEAD)" = "$revision"
  printf 'SOURCE %s %s\n' "$url" "$revision"
}

checkout "$executor" https://github.com/Zeno-L2/zeno-executor.git a14bf0e08dad8eabeb1a0e0add9bbaf1e2adac9f
checkout "$zenon" https://github.com/digitalSloth/go-zenon.git 2835c436b197fece5c76adf4ba8dc1decca2e3ea
checkout "$ziren" https://github.com/ProjectZKM/Ziren.git c9988a098d92bc55a85ca9307c470d1f97be3337

guest="$executor/cmd/vault-batch-guest"
mkdir "$guest"
cp "$source_dir/batch-witness.go" "$guest/"
if [[ -n "$proposal_source" ]]; then
  cp "$proposal_source/guest.go" "$proposal_source/proposal.go" "$proposal_source/recorded.go" "$guest/"
  mkdir "$zenon/common/vaultvalidity"
  cp "$proposal_source/statement/statement.go" "$zenon/common/vaultvalidity/"
  (
    cd "$executor"
    go mod edit -go=1.25.4
    go build -mod=mod -trimpath -o "$work/proposal-witness" ./cmd/vault-batch-guest
  )
else
  cp "$source_dir/guest.go" "$guest/"
fi
cp "$source_dir/ziren/hash-ziren.go" "$zenon/common/crypto/"
python3 -B - "$zenon/common/crypto/hash.go" "$zenon/common/errors.go" "$zenon/rpc/server/errors.go" <<'PY'
from pathlib import Path
from sys import argv

path = Path(argv[1])
original = """func Hash(data ...[]byte) []byte {
\td := sha3.New256()
\tfor _, item := range data {
\t\td.Write(item)
\t}
\treturn d.Sum(nil)
}"""
text = path.read_text(encoding="utf-8")
if text.count(original) != 1:
    raise SystemExit("Pinned SHA3-256 implementation differs")
path.write_text(text.replace(original, """func Hash(data ...[]byte) []byte {
\treturn zirenSHA3(data...)
}"""), encoding="utf-8")

# The error interface only needs its two methods. Avoid starting the RPC
# subscription ID generator in the guest, which has no entropy source.
server = Path(argv[3]).read_text(encoding="utf-8")
server_interface = (
    "type Error interface {\n"
    "\tError() string  // returns the message\n"
    "\tErrorCode() int // returns the code\n}"
)
if server.count(server_interface) != 1:
    raise SystemExit("Pinned RPC error interface differs")
path = Path(argv[2])
text = path.read_text(encoding="utf-8")
import_line = '\t"github.com/zenon-network/go-zenon/rpc/server"\n'
embedded_error = "\tserver.Error\n"
if text.count(import_line) != 1 or text.count(embedded_error) != 1:
    raise SystemExit("Pinned common error type differs")
path.write_text(text.replace(import_line, "").replace(embedded_error, "\terror\n\tErrorCode() int\n"), encoding="utf-8")
PY

runtime="github.com/ProjectZKM/Ziren/crates/go-runtime/zkvm_runtime"
(
  cd "$executor"
  go mod edit -go=1.25.4 \
    -require "$runtime@v0.0.0-20240817102429-2faba0888c02" \
    -replace "$runtime=../../ziren/crates/go-runtime/zkvm_runtime"
  GOOS=linux GOARCH=mipsle GOMIPS=softfloat CGO_ENABLED=0 \
    go list -mod=mod -tags=ziren -deps ./cmd/vault-batch-guest \
    | awk '$0 == "github.com/zenon-network/go-zenon/rpc/server" { linked = 1 } END { exit linked }'
)
# A program counter is enough to locate a failed guest instruction.
python3 -B - "$ziren/crates/prover/src/lib.rs" <<'PY_PROVER'
from pathlib import Path
from sys import argv

source = Path(argv[1])
text = source.read_text(encoding="utf-8")
run = "        runtime.run_fast()?;\n        Ok((ZKMPublicValues::from"
if text.count(run) != 1:
    raise SystemExit("Pinned Ziren execution entry point differs")
source.write_text(text.replace(run, """        if let Err(error) = runtime.run_fast() {
            eprintln!("BATCH_GUEST_ERROR_PC {:x}", runtime.state.pc);
            eprintln!("BATCH_GUEST_ERROR_PC {:x}", runtime.registers()[31].saturating_sub(8));
            return Err(error);
        }
        Ok((ZKMPublicValues::from"""), encoding="utf-8")
PY_PROVER

# Emit shard geometry before the opening so interrupted runs retain the boundary.
python3 -B - "$ziren/crates/core/machine/src/utils/prove.rs" <<'PY_SHARDS'
from pathlib import Path
from sys import argv

source = Path(argv[1])
text = source.read_text(encoding="utf-8")
anchor = "                                let cluster_widths = shard_clusters.cluster_widths(&record);"
if text.count(anchor) != 1:
    raise SystemExit("Pinned Ziren shard opening entry point differs")
source.write_text(text.replace(anchor, """                                tracing::info!(
                                    shard = record.public_values.shard,
                                    memory_init = record.global_memory_initialize_events.len(),
                                    memory_finalize = record.global_memory_finalize_events.len(),
                                    "BATCH_PROOF_SHARD",
                                );
""" + anchor), encoding="utf-8")
PY_SHARDS

# serde_json already appears in this package's pinned dependency lock.
sed -i '/^\[build-dependencies\]$/a serde_json = "1.0"' "$ziren/examples/simple-go/host/Cargo.toml"
if [[ -n "${2:-}" ]]; then
  sed -i '/^zkm-sdk = { workspace = true }$/a zkm-prover = { workspace = true }\nbincode = "1.3.3"' \
    "$ziren/examples/simple-go/host/Cargo.toml"
  python3 -B - "$ziren/examples/Cargo.lock" <<'PY_LOCK'
from pathlib import Path
from sys import argv

lock = Path(argv[1])
text = lock.read_text(encoding="utf-8")
prefix = 'name = "simple-go-host"\nversion = "1.1.0"\ndependencies = [\n'
start = text.index(prefix)
end = text.index("\n]", start)
package = text[start:end]
prover_anchor = ' "zkm-sdk",'
codec_anchor = ' "byteorder",'
if package.count(prover_anchor) != 1 or package.count(codec_anchor) != 1:
    raise SystemExit("Pinned example dependency list differs")
package = package.replace(prover_anchor, ' "zkm-prover",\n' + prover_anchor)
package = package.replace(codec_anchor, ' "bincode",\n' + codec_anchor)
lock.write_text(text[:start] + package + text[end:], encoding="utf-8")
PY_LOCK
fi
cp "$source_dir/ziren/build.rs" "$ziren/examples/simple-go/host/build.rs"
cp "$host_source" "$ziren/examples/simple-go/host/src/main.rs"
go version
(
  cd "$ziren"
  rustc --version
  ZENO_GUEST_DIR="$guest" CARGO_TARGET_DIR="$work/target" \
    cargo build --locked --release --manifest-path examples/Cargo.toml --package simple-go-host
)
test -x "$work/target/release/simple-go-host"
elf="$(find "$work/target/release/build" -type f -name vault-guest)"
test -f "$elf"
# Ziren discovers its Go scheduler and exit adapters through the ELF symbols.
go tool nm "$elf" | awk '
  $3 == "runtime.gcenable" { runtime_gc = 1 }
  $3 == "zkvm.RuntimeExit" { journal_exit = 1 }
  END { exit !(runtime_gc && journal_exit) }
'
printf 'BATCH_GUEST_ELF_SHA256 '
sha256sum < "$elf" | cut -d ' ' -f 1
