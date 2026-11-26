# Zeno execution replay before a Bitcoin regtest release

The authorization fixture adds an execution check after the contract state
proof and before broadcasting the registered transaction on Bitcoin regtest.
A second process starts from empty WASM domain state and uses the same pinned
Zeno runtime as the devnet executor. Its inputs come from the isolated L1
follower, including the sender and Momentum context. It rebuilds the code
from the recorded deployment inputs.

The replay proves each Settlement batch entry under the v3 Momentum selected
by the contract authorization fixture. It uses the proven batch boundaries
to run the inputs and compares the computed pre-state, post-state, input,
receipt, event and outbox roots, the DA hash and the asset-flow summary. The
three authorization receipts must match their selected sends, contract and
outcomes. Every batch from domain genesis through the selected batch must
match.

The replay refuses a changed Settlement proof value and a substituted sender
for the successful authorization. The sender control preserves the original
input bytes and changes the separately supplied L1 context. The runtime must
produce a commitment mismatch when it executes that context.

The live capture selects its checkpoint and input context from the local
devnet node. The saved capture binds the exact authorization evidence with
SHA-256 and carries the Settlement proofs and the L1-derived input stream.
Offline replay uses those inputs and re-executes the batches without either
chain. The live L1 collection and runtime replay address separate trust inputs.

## Recorded execution

The [hosted capture](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45840819699)
at `add64cdf5c677e567dbf1f594dfff6b9b22b74c6` ran on Linux amd64 with Go 1.26.0.
Under v3 Momentum 121, the verifier re-executed seven inputs across batches
0 through 6. The non-authorizer reverted in batch 4, the authorization
succeeded in batch 5, and the second write reverted in batch 6. Every
reproduced batch matched the authenticated Settlement entry. Bitcoin Core
32.1 mined the original registered transaction and confirmed the 999,000
satoshi output after the replay checks passed.

| Retained evidence | Bytes | Digest |
| --- | ---: | --- |
| [Contract state proof and selection](authorization.json) | 10,979 | [SHA-256](authorization.sha256) |
| [L1-derived inputs and Settlement proofs](execution.json) | 29,581 | [SHA-256](execution.sha256) |

The workflow uses the two pinned files for offline replay on subsequent pushes.

## Trust assumptions

The pinned executor uses [typed L1 inputs](https://github.com/Zeno-L2/zeno-executor/blob/a14bf0e08dad8eabeb1a0e0add9bbaf1e2adac9f/internal/ledger/follower.go)
and [execution to an accepted batch boundary](https://github.com/Zeno-L2/zeno-executor/blob/a14bf0e08dad8eabeb1a0e0add9bbaf1e2adac9f/internal/batch/driver.go).
The replay shares that implementation. Its assurance relies on the recorded
L1 context, the selected checkpoint and the pinned runtime. An independently
implemented runtime, an authenticated mainnet checkpoint and a succinct
execution validity proof remain separate requirements.

Mainnet release remains `NO_GO`. The phase-one execution profile relies on
the authorized executor's attestation at Settlement. The Zeno protocol
specification requires an on-chain `ZK_VALIDITY` profile for mainnet.
[Phase-one mainnet gate](https://github.com/Zeno-L2/zeno-docs/blob/d74ba1fc4b01583af0daf33a2dedfc84ea2b9419/phase-1/SPEC.md#L58-L64)
