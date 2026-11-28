#!/usr/bin/env bash
set -euo pipefail

work="$(cd "$1" && pwd)"
source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ziren=https://raw.githubusercontent.com/ProjectZKM/Ziren/c9988a098d92bc55a85ca9307c470d1f97be3337
umask 077

fetch() {
  local resource="$1" name="$2" bytes="$3" digest="$4"
  curl --disable --fail --silent --show-error --proto '=https' \
    --connect-timeout 10 --max-time 25 --max-filesize "$bytes" --output "$name" "$ziren/$resource"
  test "$(wc -c < "$name" | tr -d '[:space:]')" = "$bytes"
  printf '%s  %s\n' "$digest" "$name" | shasum -a 256 --check
}

mkdir "$work/checker"
cd "$work/checker"
fetch crates/recursion/gnark-ffi/go/go.mod go.mod 1394 7332487ec4d8c784cf9d09bf074daa3f4163c7ea734a0c94c0cb53cf1a6b0c73
fetch crates/recursion/gnark-ffi/go/go.sum go.sum 6574 694db5b7e4edb5d3033a8b6f0b06bdb7d7c69a4fa013184a15f7e8a497527ccf
fetch crates/verifier/bn254-vk/groth16_vk.bin groth16_vk.bin 428 b3e14fe98042c3ff6f219172965bddd8dda586c556adac9b4acbd285e13d3cdb
cp "$source_dir/verify.go" .
GOTOOLCHAIN=local GOWORK=off go build -mod=readonly -trimpath -o verify-groth16 verify.go
