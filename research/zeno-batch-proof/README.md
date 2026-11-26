# Zeno devnet batch execution proof

This experiment selects a three-input batch on the isolated Zeno devnet
(chain 69, WASM domain 1). The capture pauses its own executor, submits
three calls and waits for them to become eligible before restarting the
executor. The calls must enter one batch in this order.

| Position | Call | Expected receipt |
| --- | --- | --- |
| 0 | Non-authorizer tries to set the Bitcoin target commitment | REVERT, abort code 1 |
| 1 | Authorizer sets the commitment | SUCCESS |
| 2 | Authorizer attempts a second write | REVERT, abort code 2 |

The capture binds the batch to a selected v3 Momentum and replays every
batch from empty WASM domain state. It exports the selected batch's
Settlement inclusion proof, complete pre-state leaf set, as-deployed
module and every assigned input, including its L1 context. The pre-state
leaf set must reproduce the committed pre-state root.

## Recorded devnet transition

The [capture run](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45854186810)
at `65ae5598fc1a5d9abc0567f4649b845fb688412c` selected batch 4 under
v3 Momentum 141. The batch contains global input indices 4 through 6
and starts from four pre-state leaves. Native replay matched five
Settlement entries from empty state across seven inputs. It refused a
modified Settlement proof and a substituted authorization sender.

| Retained evidence | Bytes | Digest |
| --- | ---: | --- |
| [Authorization and checkpoint](authorization.json) | 11,067 | [SHA-256](authorization.sha256) |
| [L1-derived input stream and Settlement proofs](execution.json) | 24,287 | [SHA-256](execution.sha256) |
| [Complete batch witness](batch.json) | 12,924 | [SHA-256](batch.sha256) |

## Statement and verification

The Go guest verifies Settlement inclusion against the selected state root
and checks the batch's eligibility at the recorded height. It opens the
entire pre-state and runs all three calls through the pinned Zeno batch
driver and WASM interpreter. The computed batch must match the Settlement
commitment, including its input range, pre-state, post-state, input,
receipt, event and outbox roots, data availability hash and asset-flow
summary. Each receipt must also match the fixture's ordered outcomes.

The guest uses an in-memory adapter for the module and DA bundle. It
accelerates SHA3-256 with the Ziren sponge syscall and checks that path
against Go's SHA3-256 at padding boundaries during the smoke execution.
The guest ELF retains the symbols used by Ziren's Go scheduler and
public journal exit adapters.
The guest supplies the standard Linux errors for absent file paths and terminal devices.
The guest preserves the node error interface's method set without linking
the RPC server or starting its subscription ID generator.
The native capture independently compares the resulting commitments using
the unmodified executor and node sources.

A verifier selects the exact batch witness by SHA-256. The guest commits
that digest after the checks pass. The proof verification command rebuilds
the verifying key from the guest ELF, verifies the serialized Ziren core
proof and requires the public journal to equal the selected digest. Guest
execution controls omit an input, reorder inputs, substitute the successful
call's sender and omit a pre-state leaf. The proof verifier also checks
that changing the public journal invalidates verification.

## Scope

The selected Momentum checkpoint and the full L1 input context come from
the isolated devnet capture. Phase 1's input commitment covers the
canonical input bytes. The recorded caller and Momentum context require
the capture's L1 provenance. The experiment's public witness includes those
fields, and the journal binds them to the replayed execution.

The result is scoped to the selected Phase 1 batch, the pinned runtime and the
recorded verifier. Mainnet checkpoint authentication, Phase 2 Settlement
verification, asset-backed Bitcoin release and witness encryption remain
open.

[Pinned Zeno Phase 1 specification](https://github.com/Zeno-L2/zeno-docs/blob/d74ba1fc4b01583af0daf33a2dedfc84ea2b9419/phase-1/SPEC.md)
[Zeno batch driver](https://github.com/Zeno-L2/zeno-executor/blob/a14bf0e08dad8eabeb1a0e0add9bbaf1e2adac9f/internal/batch/driver.go)
[Ziren Go guest support](https://docs.zkm.io/dev/guest-program.html#go-example)
