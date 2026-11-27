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

The [BN254 outer proof job](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45911549775/job/140220872977)
verified the outer proof in 899.92 seconds. The successful
[Groth16 job](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45916994671/job/140238569759)
consumed that saved output by digest. The runner generated the wrapper with the
published proving key in on-demand FFT mode and verified it with the pinned
Ziren v2.0.0 verifying key. The [Groth16 fixture](groth16-fixture.json) preserves
the proof, journal, guest key and public inputs. The
[fixture checksum](groth16-fixture.sha256) covers the exact JSON bytes.

The devnet node adds `VerifyRecordedBatch` to the embedded Settlement contract.
The method uses the historical journal configured in the node and accepts a
256-byte proof with three BN254 points. It verifies the proof before writing a
64-byte receipt containing the journal and the proof SHA-256. The transaction
client checks the embedded receive codes for the original proof, the changed
journal, a changed curve point and a repeat submission. It verifies the receipt
or its absence against a v3 Momentum state root and checks that the producer
agrees with the RPC node on that Momentum.

## Recorded devnet verification

The [receipt verification run](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45919348117/job/140246094548)
used the verified Groth16 fixture at source revision
`a021970402b0db900d4c47a6d66e50fc6a33f7be` on isolated chain ID 69. The raw
256-byte proof has SHA-256
`21e7fe65b2559c9304a60872f5f03e6be143c26c74cd4db287a73b8c97dc6ce8`.

| Submission | Momentum height | Receive code | Receipt state |
| --- | ---: | ---: | --- |
| Changed journal | 15 | 2 | Absent |
| Changed proof point | 20 | 2 | Absent |
| Original proof | 25 | 1 | Written |
| Repeat proof | 30 | 2 | Same value |

The accepted send block is
`4e7bb46ae8aa738a05185db1bcb56559b79632aad8f042ff7d7a9b883272fbab`.
The receipt was proven under the v3 Momentum state root
`b6568a179061115d4a8a0660fb613b772d572dc57ec93d0a529623ed7b7a9ef9`.
The [devnet evidence](devnet-evidence.json) preserves the send and receive block
hashes, Momentum hashes, receipt values and raw state proofs for all four
submissions. All four proofs were also checked independently against their
recorded state roots. The [evidence checksum](devnet-evidence.sha256) covers
the exact JSON bytes.

Each call carried 420 bytes of ABI data and used 73,500 Plasma under the
prototype's fixed tariff.

## Statement scope

The recorded guest checks a Phase 1 Settlement inclusion proof for a batch that
already exists. The historical verification experiment uses that exact statement.
Pre-acceptance validity in `SubmitBatch` requires a different guest that receives
the proposed batch and binds its public outputs to the input hash chain tracked
by Settlement.

[Ziren verifier format](https://docs.zkm.io/dev/verifier.html)
[Pinned Ziren release source](https://github.com/ProjectZKM/Ziren/tree/c9988a098d92bc55a85ca9307c470d1f97be3337)
