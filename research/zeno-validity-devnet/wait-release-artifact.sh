#!/usr/bin/env bash
set -euo pipefail

mode="$1"
case "$mode" in
  proposal)
    name=zeno-validity-release-proposal
    limit=102400
    wait_seconds=4500
    ;;
  groth16)
    name=zeno-validity-release-groth16
    limit=12288
    wait_seconds=17400
    ;;
  *) exit 2 ;;
esac

test "${GITHUB_RUN_ATTEMPT:-}" = 1
endpoint="repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
deadline=$((SECONDS + wait_seconds))
while ((SECONDS < deadline)); do
  if jobs="$(timeout 15s gh api "$endpoint/attempts/1/jobs?per_page=20")"; then
    failed="$(jq -r --arg mode "$mode" '
      .jobs[] | select(.status == "completed" and .conclusion != "success") |
      select(if $mode == "proposal" then .name == "Isolated devnet and regtest"
             else (.name == "core" or .name == "compress" or .name == "outer" or .name == "groth16") end) | .name
    ' <<<"$jobs")"
    if [[ -n "$failed" ]]; then
      printf 'Required job did not succeed: %s\n' "$failed" >&2
      exit 1
    fi
    if jq -e --arg mode "$mode" --arg head "$GITHUB_SHA" '
      any(.jobs[]; .head_sha == $head and
        (if $mode == "proposal" then .name == "Isolated devnet and regtest" and
         any(.steps[]; .name == "Verify the persisted devnet before proving" and .conclusion == "success")
         else .name == "groth16" and .conclusion == "success" end))
    ' <<<"$jobs" >/dev/null && artifacts="$(timeout 15s gh api "$endpoint/artifacts?per_page=20")"; then
      id="$(jq -r --arg name "$name" --arg head "$GITHUB_SHA" --argjson run "$GITHUB_RUN_ID" --argjson limit "$limit" '
        [.artifacts[] | select(.name == $name and .workflow_run.id == $run and .workflow_run.head_sha == $head and
          .expired == false and .size_in_bytes > 0 and .size_in_bytes <= $limit and
          (.digest | type == "string" and test("^sha256:[0-9a-f]{64}$")))] |
        if length == 1 then .[0].id else empty end
      ' <<<"$artifacts")"
      if [[ "$id" =~ ^[1-9][0-9]*$ ]]; then
        printf 'id=%s\n' "$id" >> "$GITHUB_OUTPUT"
        printf 'Selected %s artifact %s for source %s\n' "$name" "$id" "$GITHUB_SHA"
        exit 0
      fi
    fi
  fi
  sleep 30
done
printf 'Timed out waiting for %s\n' "$name" >&2
exit 1
