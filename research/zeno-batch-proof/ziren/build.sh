#!/usr/bin/env bash
set -euo pipefail

work="$1"
source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
cp "$source_dir/guest.go" "$source_dir/batch-witness.go" "$guest/"
cp "$source_dir/ziren/hash-ziren.go" "$zenon/common/crypto/"
python3 -B - "$zenon/common/crypto/hash.go" "$ziren/crates/go-runtime/zkvm_overlay/runtime/sys_linux_mipsx.s" <<'PY'
import hashlib
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

overlay = Path(argv[2])
text = overlay.read_text(encoding="utf-8")
start = text.index("TEXT runtime·futex(SB)")
end = text.index("\n\n\n// int32 clone", start)
old_futex = hashlib.sha256(text[start:end].encode()).hexdigest()
if old_futex != "8b8ea773db91545c855d5159375941c853eb4276c45d9dd256f5213a8d0985d1":
    raise SystemExit("Pinned futex implementation differs")
# The VM's clone stub creates no worker threads. Preserve the nonblocking
# futex results for an empty wait queue or a changed futex word.
futex = """TEXT runtime·futex(SB),NOSPLIT,$0-28
\tMOVW\top+4(FP), R4
\tMOVW\t$129, R5 // FUTEX_WAKE_PRIVATE
\tBEQ\tR4, R5, futexwake
\tMOVW\t$128, R5 // FUTEX_WAIT_PRIVATE
\tBNE\tR4, R5, futexunsupported
\tMOVW\taddr+0(FP), R4
\tMOVW\t(R4), R5
\tMOVW\tval+8(FP), R6
\tBEQ\tR5, R6, futexunsupported
\tMOVW\t$-11, R2 // EAGAIN
\tMOVW\tR2, ret+24(FP)
\tRET
futexwake:
\tMOVW\tR0, ret+24(FP)
\tRET
futexunsupported:
\tUNDEF"""
overlay.write_text(text[:start] + futex + text[end:], encoding="utf-8")
PY

runtime="github.com/ProjectZKM/Ziren/crates/go-runtime/zkvm_runtime"
(
  cd "$executor"
  go mod edit -go=1.25.4 \
    -require "$runtime@v0.0.0-20240817102429-2faba0888c02" \
    -replace "$runtime=$ziren/crates/go-runtime/zkvm_runtime"
)
cp "$source_dir/ziren/build.rs" "$ziren/examples/simple-go/host/build.rs"
cp "$source_dir/ziren/host.rs" "$ziren/examples/simple-go/host/src/main.rs"
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
printf 'BATCH_GUEST_ELF_SHA256 '
sha256sum < "$elf" | cut -d ' ' -f 1
