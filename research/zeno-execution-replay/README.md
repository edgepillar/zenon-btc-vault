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

The pinned executor uses [typed L1 inputs](https://github.com/Zeno-L2/zeno-executor/blob/a14bf0e08dad8eabeb1a0e0add9bbaf1e2adac9f/internal/ledger/follower.go)
and [execution to an accepted batch boundary](https://github.com/Zeno-L2/zeno-executor/blob/a14bf0e08dad8eabeb1a0e0add9bbaf1e2adac9f/internal/batch/driver.go).
The replay shares that implementation. Its assurance relies on the recorded
L1 context, the selected checkpoint and the pinned runtime. An independently
implemented runtime, an authenticated mainnet checkpoint and a succinct
execution validity proof remain separate requirements.
