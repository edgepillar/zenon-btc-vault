# Live proof-bearing SubmitBatch

This experiment applies the [proposed batch statement](../zeno-validity-statement/README.md)
to three fresh `CallL2` inputs on isolated chain 69. The pinned devnet builds
the PR 12 Settlement overlay with the verified proposal guest key. Four setup
batches deploy the immutable authorizer and buy its storage using the existing
devnet executor. The authorizer runs under the pinned Go WASM runtime.

## From pending inputs to proof

The capture stops the disposable executor after the fourth setup batch. It
queues three calls, waits for their L1 receives and selects a v3 Momentum. At
that checkpoint, the input-sequence cursor is at batch 4 with four consumed
inputs. The new batch is absent, and the three outstanding ordinals point to
the captured sends. The witness builder checks these values against the
Momentum state root and replays all four setup batches from their L1 inputs.
It uses the resulting pre-state and the three confirmed source inputs to
produce the prospective commitment and public journal.

The devnet is rebuilt on a fresh runner from the pinned source and a cold
checkpoint. The checkpoint manifest identifies files from the five nodes'
chain, consensus, rollback cache and state-tree databases. Each unique object
is stored once by its digest. The fresh runner verifies the manifest, restores
only those databases, and checks the same genesis, v3 checkpoint and pending
ordinals before the proof run can begin.

The proof workflow binds the exact witness, guest and journal. It produces a
Core proof, verifies the serialized proof, aggregates it, verifies the BN254
outer proof and generates the 256-byte Groth16 wrapper. The wrapper is verified
with the pinned Ziren v2.0.0 release key. Successful jobs publish individual
outputs so later stages can reuse them by digest.

## Captured checkpoint

The capture job used source revision `02a4833d5c5e5ad77de8352672b5ea56f1f00684`.
It selected Momentum 99 with hash
`225b785c49e5cd3fa8fd15b7aac661491f99b222978c3e7f1af423d5ebd65aed`
and root `53b9b175f1a5d44783791e8c7cf05a501a01832c83b05ea2377e829df6c46bfd`.
The three calls were admitted and batch 4 was absent. The cold checkpoint
contains 80 file references to 40 distinct objects.
[Capture job](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46026658716/job/140599679686)

The preflight restored the exact checkpoint on a fresh runner and proved the
same ordinals, batch absence and cursor at the resumed chain. It reached
Momentum 120 with the proposal still pending. The separate proof tool build
reproduced the proposal guest ELF and pinned guest key.
[Restore preflight](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46026658716/job/140611460946) · [Proof tools](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46026102217/job/140597785715)

| Item | Value |
| --- | --- |
| Proposal witness SHA-256 | `a2b3f422f2e8db88acf1807b8b2f5a4d51bcaf811db916a8650e914a90dfff12` |
| Checkpoint TAR SHA-256 | `01e6993848bcce8aa91c584d56671dd75281de5b209775fe25da75f767c7208d` |
| Journal | `42b7c623baa319b2d79e95f38b183bd9a44feb4e25ae8cdcbead3ffc06399f03` |
| Ending input link | `16e9b3992026ad9253a182f01e68a0eef02b5d38cc5af55e600e68b066cb340b` |

## Verified proof stages

The proving run selected controller revision `9d66cb2b376c61d143e8534e0c39c0fcf2e43525`.
It verified the serialized Core proof for the new journal and aggregated all
167 shards. The resulting recursive proof passed read-back verification for
the same journal and guest key.

| Stage | Bytes | SHA-256 | Job |
| --- | ---: | --- | --- |
| Ziren Core | 146,349,466 | `2189de0a3dec19aec6f2d01ff1dfdc2b2024f506e93cd19628a8e082999af85b` | [Core](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46030531390/job/140612973697) |
| Recursive proof | 290,588 | `83a41ad0a140c56a18f2390604d9f003418e61b709ab6cf882293492f0ffcb9a` | [Recursion](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46030531390/job/140642006726) |

The verified BN254 outer proof has SHA-256
`78d2dcb49710d6e825a776d787690d556581d50eac8238d3b5cfb73deb2001af`.
[Outer proof job](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46030531390/job/140667915490)

The 256-byte Groth16 proof passed verification against the pinned release key.
Its raw SHA-256 is
`a1e1b629a6d38483e2c11174e28b948507e61973cdb74fc5a21d5f168a1dd30d`.
The fixture JSON SHA-256 is
`6067cb907a1bd3fdd9ff23976f51d80d86886c0c1bb71d35e9f602ed56e1e839`.
[Groth16 job](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46030531390/job/140678025767) · [Fixture artifact](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46030531390/artifacts/15223754159)

## Acceptance evidence

The final runner verifies the checkpoint and journal again and submits the
proof once through Settlement's `SubmitBatch` with the configured devnet
executor. The client checks the embedded receive status and reads the batch,
cursor and input-chain anchor under the receive Momentum's state root. The
producer and RPC node must agree on that Momentum. The accepted batch must
match all seven proposed commitment hashes. The cursor must advance to batch
5 with seven consumed inputs. The stored anchor must contain the new cursor
and the ending input link calculated before proving.

The acceptance run used source revision `d7fa4ab2bbf765f24c28baf20625a60eddd87e1b`.
It confirmed the valid proof-bearing send at Momentum 122 with receive code `1`.
The confirmed send payload matched the signed payload byte for byte. It carried
644 bytes and used 64,792 Plasma under the prototype's tariff.
[Acceptance job](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46052640002/job/140687469636)

| Checkpoint | Batch 4 | Next batch ID | Consumed inputs | Input-chain anchor |
| --- | --- | ---: | ---: | --- |
| 99, before proving | Absent | 4 | 4 | Absent |
| 120, after restoring | Absent | 4 | 4 | Absent |
| 122, after acceptance | Present | 5 | 7 | Ending link stored |

| Accepted transaction | Value |
| --- | --- |
| Send hash | `0dd1bb08d255f3fc5725dfec507a0ca749bac3df0b8935b56e13c066b1edcfce` |
| Receive hash | `072704a20271978322dc3789fa6a3e6bd1928aa1048dd67085ad8a7e22631d46` |
| Momentum hash | `c42579ddf276aa71c0212d2fc1dc5fbb08f54864e7b91d57a5d04293cb2aa871` |
| Momentum state root | `4a0de4b164f3e8a3947df16b0fd3e0a710004464b7bf68f968338a81b4ab0ab2` |

An independent SHA3-256 reconstruction checked all 13 state proofs, the three
assigned send hashes, the full accepted commitment, cursor, ending anchor and
Groth16 public inputs. The earlier acceptance run reported the hexadecimal
encoding length. The final run reused the same checkpoint and proof and
measured the confirmed payload bytes directly.
[Earlier acceptance](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46052023492/job/140685423675)

The exact [acceptance record](acceptance.json) and [Groth16 fixture](groth16-fixture.json)
are retained with their [acceptance checksum](acceptance.sha256) and
[fixture checksum](groth16-fixture.sha256). The archived acceptance artifact is
bound to run `46052640002`, attempt 1.
[Acceptance artifact](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46052640002/artifacts/15224783871)

The protocol profile retains the Phase 1 setup batches and withdrawal delay.
The result is scoped to the three-call authorizer profile and the devnet
overlay. [Phase 2 outline](https://github.com/Zeno-L2/zeno-docs/blob/d74ba1fc4b01583af0daf33a2dedfc84ea2b9419/phase-2/SPEC.md)

## Validity-backed Bitcoin regtest release

The [release workflow](../../.github/workflows/zeno-validity-regtest-release.yml)
combines the validity proof with the existing fixed-output regtest registration.
Bitcoin Core first funds the vault and validates a signed transaction with a
single 999,000-satoshi payout. The authorization call commits to that
registration's Bitcoin genesis, funding outpoint, recipient script and amount.
The witness builder exports an absence proof for the prior authorization slot
and post-state proofs for the module hash, immutable policy and target slot.
An independent verifier checks those paths before the proof run.

The capture and release job keeps the two local chains on its runner. Dedicated
jobs generate and verify the proofs on fresh runners. They take the qualified
proposal from the current run and pass verified proof files by digest.

After the proof stages, the restored devnet accepts the same commitment through
Settlement. The runner checks the batch entry at its eligible Momentum and
binds the contract proofs to the accepted post-state root. Bitcoin Core reopens
the original regtest tip, checks the registered transaction again and confirms
the exact bytes. A successful run records the state roots, both chain receipts
and the SHA-256 of the original signed transaction. The regtest data and signed
transaction stay on the runner. The experiment uses a runner-held registration
and the devnet's configured withdrawal delay.

### Verified run

The [successful workflow](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46082713518)
tested revision `ac9e3c868c63c44528f28c241656782408750db3` on GitHub-hosted
Ubuntu 24.04 runners. The devnet returned receive code `1` for the 256-byte
Groth16 proof submitted with batch 4. The acceptance job retained the same
batch entry and input-chain anchor at the eligible checkpoint. [Settlement and regtest job](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46082713518/job/140785191917)

| Checkpoint | Momentum | Batch 4 | Next batch | Consumed inputs |
| --- | ---: | --- | ---: | ---: |
| Pending proposal | 97 | Absent | 4 | 4 |
| Resumed devnet | 118 | Absent | 4 | 4 |
| Accepted by Settlement | 120 | Present | 5 | 7 |
| Eligible under the configured delay | 149 | Present | 5 | 7 |

| Receipt field | Value |
| --- | --- |
| Settlement send | `108aff35b904c98918411483d95870215cfa8f6aae13bb75ab01dae3f9c1731b` |
| Accepted batch post-state root | `8d7ee602328f66bb4b1f843ddbab397a589e43c634236190a26c82b7a05f28a9` |
| Eligible Momentum | `cd646bbe9a5d65980100d966a45a09846b72d9440fb6f3ff5d2061080e8e09ce` |
| Eligible Momentum state root | `9552ae06dee5d8fe9915a83c71822949b870f27f1fa0130624b7a0ee700c44a4` |
| Bitcoin target authorization | `ce1c323055ddde77b4e810a97f9f9bae0f3db8a4f95199fd18eb7f76750246e6` |
| Regtest funding outpoint | `5be4dd66ec28b6efb5d97822d51805c598a17040b519755da1876a6c9ed5f6b7:1` |
| Confirmed regtest transaction | `bf1279d08a4f7115b045e77dd05c41694e80794186be10e8c360e067874f1fc4` |
| Regtest block 103 | `71ddf1f648c8d3c8252947b62d73be04dca2e71f90f5c0530926b1439b9f0f70` |
| Payout | 999,000 satoshis with one confirmation |
| Signed transaction SHA-256 | `e4cb28275d459840252d21b5f25511b55a133c9445c1cc74e515a28411770b60` |

The registration and confirmation logs contain the same SHA-256 for the
signed transaction. Re-encoding the Bitcoin target in the release record
reproduces the contract's authorization commitment. The rendered release
JSON reproduces the checksum reported by the runner.

| File | SHA-256 |
| --- | --- |
| `acceptance.json` | `4625b9db6284f24bb92ec1b2cab1c85c06acdc21d4928cd01014583b683513bb` |
| `fixture.json` | `8a607544b60076189b0d2aae3c5401c91a6820f67638b5836a9417e78743a31c` |
| `release.json` | `9c55b345fba3c395e92be21b794127536f680e0c931957e79d987ac43fdb4edb` |

The workflow retained the proposed contract proofs, the Groth16 fixture, the
Settlement state proofs and the Bitcoin release record. [Proposal evidence](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46082713518/artifacts/15238493491) · [Groth16 fixture](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46082713518/artifacts/15244437809) · [Settlement acceptance](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46082713518/artifacts/15244812672) · [Bitcoin release](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46082713518/artifacts/15245135382)

The initial run captured its own proposal. GitHub reported a lost runner
connection during Core proving. The successful run started with a fresh
registration and ran each proof stage on a dedicated runner. [Initial run](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46071669878/job/140749819721)
