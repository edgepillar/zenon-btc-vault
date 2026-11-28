#!/usr/bin/env bash
set -euo pipefail

work="$(cd "$1" && pwd)"
research="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
host="$work/ziren/examples/simple-go/host/src/main.rs"
binary="$work/target/release/simple-go-host"
guest="$work/zeno/zeno-executor/cmd/vault-batch-guest"
umask 077

test "$(df -PB1 "$work" | awk 'NR == 2 { print $4 }')" -ge "$((23 * 1024 * 1024 * 1024))"
export CARGO_HOME="$work/cargo" GOMODCACHE="$work/go-modules" GOCACHE="$work/go-build"
if ! command -v protoc >/dev/null; then
  timeout 180s sudo apt-get -q update
  timeout 180s sudo apt-get -q install -y protobuf-compiler
fi

timeout --kill-after=10s 35m bash "$research/zeno-batch-proof/ziren/build.sh" "$work" \
  "$research/zeno-settlement-proof/wrap.rs" "$research/zeno-validity-statement"
mv "$binary" "$work/validity-wrapper"

build_host() (
  cp "$1" "$host"
  cd "$work/ziren"
  ZENO_GUEST_DIR="$guest" CARGO_TARGET_DIR="$work/target" \
    timeout --kill-after=10s 8m cargo build --locked --release --manifest-path examples/Cargo.toml --package simple-go-host
)

build_host "$research/zeno-settlement-proof/prover.rs"
mv "$binary" "$work/validity-compressor"
build_host "$research/zeno-batch-proof/ziren/host.rs"
mv "$binary" "$work/simple-go-host"

elf="$(find "$work/target/release/build" -type f -name vault-guest)"
printf '%s  %s\n' "$GUEST_ELF" "$elf" | sha256sum --check --strict
timeout 90s "$work/validity-wrapper" guest-key | grep -Fx "SETTLEMENT_GUEST_KEY $GUEST_KEY"
timeout 90s "$work/simple-go-host" guest-key | grep -Fx "BATCH_GUEST_KEY $GUEST_KEY"

record_tool() {
  local output="$1" name="$2" maximum="$3" actual digest
  actual="$(stat -c%s "$work/$name")"
  test "$actual" -gt 0 && test "$actual" -le "$maximum"
  digest="$(sha256sum < "$work/$name" | cut -d ' ' -f 1)"
  printf 'VALIDITY_TOOL_SHA256 %s %s\n' "$name" "$digest"
  printf '%s_sha256=%s\n' "$output" "$digest" >> "$GITHUB_OUTPUT"
}

record_tool core simple-go-host 134217728
record_tool compressor validity-compressor 268435456
record_tool wrapper validity-wrapper 268435456
