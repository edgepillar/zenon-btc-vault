# Zeno Settlement verification experiment

This experiment targets the embedded Settlement verification question in the
[Phase 2 outline](https://github.com/Zeno-L2/zeno-docs/blob/d74ba1fc4b01583af0daf33a2dedfc84ea2b9419/phase-2/SPEC.md)
and its [R4 research item](https://github.com/Zeno-L2/zeno-docs/blob/d74ba1fc4b01583af0daf33a2dedfc84ea2b9419/research/proving/R4-onchain-snark-verify.md).
The statement comes from [PR 10](https://github.com/edgepillar/zenon-btc-vault/pull/10),
where the pinned guest re-executed all three inputs of the recorded batch 4.

The recursion job downloads the original 171-shard Core proof from the pinned
successful run and verifies its file digest, journal and rebuilt guest key.
It aggregates the retained shards and opens the resulting proof in a separate
process. The verifier requires the exact batch journal and rejects a change to
that journal.

The recorded guest checks a Phase 1 Settlement inclusion proof for a batch that
already exists. The historical verification experiment uses that exact statement.
Pre-acceptance validity in `SubmitBatch` requires a different guest that receives
the proposed batch and binds its public outputs to inputs held by Settlement.
