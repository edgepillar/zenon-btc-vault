#!/usr/bin/env bash

# Use spill space only inside this hosted proof job's disposable workspace.
enable_prover_swap() {
  local work="$1" free_bytes memory_kib swap_gib
  free_bytes="$(df -PB1 "$work" | awk 'NR == 2 {print $4}')"
  test "$free_bytes" -ge "$((12 * 1024 * 1024 * 1024))"
  memory_kib="$(awk '/^(MemTotal|SwapTotal):/ { sum += $2 } END { print sum }' /proc/meminfo)"
  swap_gib="$((30 - memory_kib / 1024 / 1024))"
  if [ "$swap_gib" -gt 0 ] && [ "$free_bytes" -ge "$(((swap_gib + 12) * 1024 * 1024 * 1024))" ]; then
    SETTLEMENT_SWAP="$work/prover-swap"
    fallocate -l "${swap_gib}G" "$SETTLEMENT_SWAP"
    chmod 0600 "$SETTLEMENT_SWAP"
    sudo mkswap "$SETTLEMENT_SWAP"
    if sudo swapon "$SETTLEMENT_SWAP"; then
      trap 'sudo swapoff "$SETTLEMENT_SWAP"' EXIT
    fi
  fi
  awk '/^(MemTotal|SwapTotal):/ { print }' /proc/meminfo
}
