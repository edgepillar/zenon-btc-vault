#!/usr/bin/env bash
set -euo pipefail

stage="$1"
work="$2"
research="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$research/zeno-settlement-proof/prover-resources.sh"
cd "$work"
umask 077

check_digest() {
  local expected="$1" name="$2"
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]]
  printf '%s  %s\n' "$expected" "$name" | sha256sum --check --strict
}

use_prover() {
  check_digest "$1" "$2"
  chmod 0500 "$2"
}

reserve_spill() {
  test ! -e "$work/prover-swap"
  test ! -L "$work/prover-swap"
  enable_prover_swap "$work" "$1"
  if [[ "${RELEASE_PROVER_SPILL:-}" = 1 && -n "${SETTLEMENT_SWAP:-}" ]]; then
    trap 'sudo swapoff "$SETTLEMENT_SWAP"; sudo rm -- "$SETTLEMENT_SWAP"' EXIT
  fi
  test "$(awk '/^(MemTotal|SwapTotal):/ {sum += $2} END {print sum}' /proc/meminfo)" -ge "$(( $1 * 1024 * 1024 ))"
}

save_digest() {
  local digest
  digest="$(sha256sum < "$1" | cut -d ' ' -f 1)"
  printf 'VALIDITY_PROOF_SHA256 %s %s\n' "$1" "$digest"
  printf 'sha256=%s\n' "$digest" >> "$GITHUB_OUTPUT"
}

[[ "$JOURNAL" =~ ^[0-9a-f]{64}$ ]]
case "$stage" in
  core)
    check_digest "$PROPOSAL_SHA256" proposal.json
    check_digest "$STATEMENT_SHA256" statement.json
    test "$(jq -er '.journal' statement.json)" = "$JOURNAL"
    use_prover "$CORE_TOOL_SHA256" simple-go-host
    test "$(timeout 90s ./simple-go-host guest-key | awk '$1 == "BATCH_GUEST_KEY" {print $2}')" = "$GUEST_KEY"
    reserve_spill 32
    ulimit -f 524288
    /usr/bin/time -f 'VALIDITY_CORE_SECONDS %e MAX_RSS_KIB %M EXIT %x' \
      timeout --kill-after=20s 120m nice -n 8 ./simple-go-host prove proposal.json "$JOURNAL" proof.bin
    timeout --kill-after=10s 8m ./simple-go-host verify "$JOURNAL" proof.bin
    save_digest proof.bin
    ;;
  compress)
    check_digest "$INPUT_SHA256" proof.bin
    use_prover "$COMPRESSOR_SHA256" validity-compressor
    reserve_spill 32
    ulimit -f 16384
    /usr/bin/time -f 'VALIDITY_RECURSION_SECONDS %e MAX_RSS_KIB %M EXIT %x' \
      timeout --kill-after=20s 170m nice -n 8 ./validity-compressor compress-live "$JOURNAL" proof.bin compressed.bin
    timeout --kill-after=10s 7m ./validity-compressor verify-compressed "$JOURNAL" compressed.bin
    save_digest compressed.bin
    ;;
  outer)
    check_digest "$INPUT_SHA256" compressed.bin
    use_prover "$WRAPPER_SHA256" validity-wrapper
    timeout 90s ./validity-wrapper guest-key | grep -Fxq "SETTLEMENT_GUEST_KEY $GUEST_KEY"
    reserve_spill 32
    ulimit -f 262144
    /usr/bin/time -f 'VALIDITY_OUTER_SECONDS %e MAX_RSS_KIB %M EXIT %x' \
      timeout --kill-after=20s 120m nice -n 8 ./validity-wrapper outer "$JOURNAL" compressed.bin outer.bin
    save_digest outer.bin
    ;;
  groth16)
    check_digest "$INPUT_SHA256" outer.bin
    use_prover "$WRAPPER_SHA256" validity-wrapper
    timeout 90s ./validity-wrapper guest-key | grep -Fxq "SETTLEMENT_GUEST_KEY $GUEST_KEY"
    timeout --kill-after=15s 45m python3 -B "$research/zeno-settlement-proof/install-circuit.py" circuit
    reserve_spill 42
    python3 -B "$research/zeno-settlement-proof/set-fft-cache.py" circuit/groth16_pk.bin
    mkdir tmp
    export TMPDIR="$work/tmp"
    ulimit -f 2097152
    /usr/bin/time -f 'VALIDITY_GROTH16_SECONDS %e MAX_RSS_KIB %M EXIT %x' \
      timeout --kill-after=30s 150m nice -n 8 ./validity-wrapper groth16 "$JOURNAL" outer.bin circuit fixture.json
    test "$(stat -c%s fixture.json)" -le 8192
    jq -e --arg journal "$JOURNAL" --arg key "$GUEST_KEY" \
      '.journal == $journal and .guestKey == $key and (.proof | test("^[a-f0-9]{512}$"))' fixture.json >/dev/null
    save_digest fixture.json
    printf 'VALIDITY_GROTH16_FIXTURE '
    cat fixture.json
    printf '\n'
    ;;
  *) exit 2 ;;
esac
