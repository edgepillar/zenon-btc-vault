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

The successful [recursion run](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45903287946)
aggregated all 171 shards in 4,283.15 seconds on the hosted CPU. The resulting
290,588-byte proof passed read-back verification and the altered-journal control.
Its SHA-256 is `fafddd4edb48022bb35347870caccfa497c604e81b2e54c3e82f528b4944a1cb`.

The next proving stages shrink the recursive proof, wrap the shrunk proof in a
BN254 STARK and use the Ziren v2.0.0 Groth16 circuit. The three public inputs
bind the rebuilt guest key, the 253-bit digest of the journal and the release's
recursion key root. The workflow verifies the wrapper using the verifying key
published with the pinned Ziren source.

The devnet node adds `VerifyRecordedBatch` to the embedded Settlement contract.
The method uses the historical journal configured in the node and accepts a
256-byte proof with three BN254 points. It verifies the proof before writing a
64-byte receipt containing the journal and the proof SHA-256. The transaction
client checks the embedded receive codes for the original proof, the changed
journal, a changed curve point and a repeat submission. It verifies the receipt
or its absence against a v3 Momentum state root and checks that the producer
agrees with the RPC node on that Momentum.

The recorded guest checks a Phase 1 Settlement inclusion proof for a batch that
already exists. The historical verification experiment uses that exact statement.
Pre-acceptance validity in `SubmitBatch` requires a different guest that receives
the proposed batch and binds its public outputs to inputs held by Settlement.

[Ziren verifier format](https://docs.zkm.io/dev/verifier.html)
[Pinned Ziren release source](https://github.com/ProjectZKM/Ziren/tree/c9988a098d92bc55a85ca9307c470d1f97be3337)
