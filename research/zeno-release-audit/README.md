# Offline audit of the validity-backed regtest release

This verifier consumes the public files from the original
[release run](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46082713518/job/140785191917).
[The profile](profile.json) pins the source revision, run attempt, archive
digests, exact JSON files and selected Momentum observations. The audit checks
the admitted input ordinals and reconstructs the three-call journal. It then
verifies 16 Settlement state paths, four contract state paths and the
Groth16 pairing. The recorded Bitcoin target must match the authorization
proved under the accepted batch's post-state root.

The selected release checkpoint must meet the withdrawal deadline and match
the pinned height, Momentum hash and state root. The audit exercises that
predicate with the valid but early acceptance at Momentum 120 and a
conflicting hash at the eligible height of 149. Both views must be refused.

## Trust boundary

The checkpoint pins come from the original controlled devnet run and the
authenticated artifact metadata. The Bitcoin receipt records the original
runner's confirmation and checked signed-byte digest. The replay verifies
their encoding and binding to the proven authorization. A future live
release policy still needs independent producer authorization, canonical
fork selection and Bitcoin confirmation and refund reconciliation.

## Replay

Use the four artifacts listed in the profile. Place the seven pinned JSON
members in one evidence directory. The verification step reads those files
without launching nodes or requiring a wallet. Build the checker in an owned
workspace with Go 1.26.0 and the pinned Ziren dependency files. The build
script fetches and checks the public verification key and upstream Go lock;
the subsequent verification runs offline.

```sh
bash research/zeno-release-audit/build-checker.sh "$AUDIT_WORK"
export PYTHONPATH="$PWD:$PWD/research/zeno-validity-devnet:$PWD/research/zeno-contract-authorization"
python3 -B research/zeno-release-audit/audit.py "$EVIDENCE_DIR" \
  "$AUDIT_WORK/checker/verify-groth16" "$AUDIT_WORK/checker/groth16_vk.bin"
```

The [GitHub Actions workflow](../../.github/workflows/zeno-release-audit.yml)
checks the run and archive provenance before replaying the evidence.

## Verified replay

The successful audit used revision `034cdb4fed8113c14a01a911fe41964b116f3235`.
It reconstructed the journal and verified the 256-byte Groth16 proof with
SHA-256 `7e1381e0c0474c438c0fcc2e1af79cbf9aeedbfb55a343870db22e22dde4e4b0`.
All 20 state proofs passed, and the checkpoint rule rejected both mismatched
views. The receipt matched the authorized 999,000-satoshi output.
[Run summary](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46110829171#summary-140873760172)

The [audit record](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46110829171/artifacts/15249263916)
has archive SHA-256 `b6a73105ce82f72891d2a7dfd0a03a4c15af6f96f16fb88fd8fea3c4120fa026`.
