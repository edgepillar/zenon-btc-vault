# Proposed batch validity

The proposed-batch guest replays the three zero-deposit calls to the immutable
vault authorizer and checks the entire canonical batch commitment. The devnet
node derives the journal before accepting the batch. The profile uses the Go
WASM executor and the Ziren v2.0.0 verifier from the earlier experiments.
[Phase 2 outline](https://github.com/Zeno-L2/zeno-docs/blob/d74ba1fc4b01583af0daf33a2dedfc84ea2b9419/phase-2/SPEC.md)

## Public statement

Each hash uses SHA-256 and a versioned ASCII prefix terminated by a zero byte.
All integers are unsigned and big-endian. Hashes occupy 32 bytes and addresses
occupy 20 bytes. The [encoder](statement/statement.go) defines the byte order.

| Hash | Fields after the prefix |
| --- | --- |
| Starting anchor | Chain ID, domain ID, batch ID, first domain ordinal, accepted parent post-state root |
| Input link | Prior link, domain ordinal, global input index, source send hash, sender, confirming Momentum height, timestamp, Momentum hash, target, plasma limit, payload length and payload |
| Public journal | Chain ID, first domain ordinal, count, starting link, ending link, commitment length and canonical commitment |

Chain IDs, batch IDs, input indices, ordinals, Momentum heights, timestamps,
plasma limits and byte lengths are 64-bit words. The domain ID and input count
are 32-bit words. The canonical commitment includes the executor address, all
seven commitment hashes, the asset-flow summary and the version fields. Its
proof field is empty and its submitted height is zero in the journal preimage.

The guest reconstructs the pre-state root from the supplied leaves, executes all
three calls with the pinned authorizer WASM and compares the encoded result with
the proposal. Its witness carries the starting link and the source inputs. The
node derives the starting link and ordered range from Settlement's state and
derives each call and its confirmation context from the node's Momentum store.

## Settlement gate

The devnet-only node overlay enables the proof requirement for domain 1 starting
at batch 4 on chain ID 69. Earlier batches provide the attested setup state. The
first validity batch uses the starting anchor defined above. Later batches use the
ending link persisted alongside the successor cursor. The node derives links
from admitted ordinals during verification, using the inputs already stored by
the Phase 1 contract.

The experimental `SubmitBatch(bytes)` encoding carries the 256-byte Ziren
Groth16 proof in the commitment's length-prefixed proof field. The node checks
the signed submitter, expected batch ID and the parent state root, rebuilds the
journal, and verifies the proof before calling the existing acceptance logic.
Success advances the cursor and stores the ending link in the same embedded
receive. The prototype retains the base Settlement acceptance policies and its
fixed Plasma tariff.

## Qualification

The portable vector uses the archived PR 10 calls and proven parent state as
replay inputs. Its candidate executor is a public fixture address. The guest
receives the prospective commitment directly. A distinct Python encoder checks
byte parity and the node control reconstructs the same journal from assigned
ordinals, ABI-encoded sends and Momentum records in a test ledger. The latter
also checks that a missing proof or the valid historical PR 11 proof cannot
advance the cursor or write a batch. The hosted workflow verifies the serialized
Ziren Core proof and checks the altered-journal case separately.
[PR 10 batch](https://github.com/edgepillar/zenon-btc-vault/pull/10) · [PR 11 proof](https://github.com/edgepillar/zenon-btc-vault/pull/11)

The proof-bearing submission gate requires a Groth16 wrapper of the new guest's
proof. A fresh live devnet qualification starts from pending admitted calls and
checks the resulting batch entry and cursor after the signed submission.
