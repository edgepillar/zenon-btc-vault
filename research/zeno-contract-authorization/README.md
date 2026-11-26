# Zeno contract authorization for a registered regtest release

The capture combines Bitcoin Core 32.1 regtest with the pinned Zeno devnet.
Bitcoin Core validates a presigned fixed-recipient transaction against a
confirmed vault output before the Zeno call. The immutable WASM authorizer
accepts a commitment once from a designated public devnet fixture caller.
It requires a storage quota of 96 bytes and uses slot `01`.

The commitment is SHA3-256 over the following byte sequence.

| Field | Encoding |
| --- | --- |
| Tag | `zenon-btc-vault/contract-authorization` followed by `00 01` |
| Zenon chain ID and Zeno domain | Unsigned big-endian `u64` and `u32` |
| Authorizing contract ID | 32 bytes |
| Bitcoin network genesis and funding transaction ID | 32 bytes each in Bitcoin Core display byte order |
| Funding output index and payout amount | Unsigned big-endian `u32` and `u64` in satoshis |
| Recipient `scriptPubKey` | Unsigned big-endian `u16` length followed by the script bytes |

The application writes a commitment into contract state. The verifier proves
the Settlement batch entry under a selected v3 Momentum checkpoint, then
checks the contract metadata and storage proofs under that batch's
`postStateRoot`. The pinned module hash, immutable policy, contract instance,
storage slot and Bitcoin target all participate in authorization. The batch
and checkpoint bind the proof at verification time. No future batch number is
needed at contract call time.

The hosted capture checks runtime refusals for a different caller and an
attempted second write. Offline replay checks the retained proof and refuses an
ineligible checkpoint, a mismatched checkpoint, contract, module, target or
batch, and a modified leaf. Bitcoin Core mines the original registered bytes
only after the local verifier succeeds and the harness independently rebuilds
the commitment from the registration.

## Recorded capture

The [native run](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45782195624/attempts/1)
at `a142fea7e46dc0c78eab814e35cf6406ce810e26` confirmed the following results.

| Control | Observed result |
| --- | --- |
| Call from another devnet user | Reverted in batch 4 |
| Call from the designated authorizer | Succeeded in batch 5 |
| Attempted second write | Reverted in batch 6 |
| Batch 6 state proof | Rejected at Momentum 117 and accepted at Momentum 121. Eligibility began at Momentum 118. |
| Bitcoin Core 32.1 regtest | Mined the registered release and confirmed the 999,000 satoshi output |

The [capture bytes](evidence.json) match the recorded SHA-256 in
[`evidence.sha256`](evidence.sha256). The replay selects the capture by that
digest before using its recorded checkpoint and contract pins. It rechecks
both proof layers and the refusal cases without running either network. The
native run separately checked the receipt results and Bitcoin confirmation.

## Trust assumptions

The source is [Zeno devnet `cf1c9c0`](https://github.com/Zeno-L2/zeno-devnet/tree/cf1c9c0e0f0ca3b257bdbd019c5589c2c27f748f)
with its pinned dependencies. The WASM build uses the Rust SDK from that
checkout's source manifest and validates using the executor revision the
devnet runs. The capture selects checkpoints from its own
isolated producer and relies on the Phase 1 executor attestation for execution
correctness. Settlement's eligibility timer is defined in the
[pinned batch acceptance specification](https://github.com/Zeno-L2/zeno-docs/blob/d74ba1fc4b01583af0daf33a2dedfc84ea2b9419/phase-1/tickets/epic-1-l1-settlement/E1-07-submitbatch-acceptance-and-finality.md).

Mainnet release remains blocked pending independently established network
activation, canonical Momentum finality, and an execution assurance policy
for the committed L2 transition. The current
[SPV verification contract](https://github.com/edgepillar/zenon-spv/blob/f058474d5f35cfa08cdccbf153d6fb9fbf9ab649/docs/verification-contract.md)
covers v1/v2 header and inclusion checks with an explicit external schedule
and local depth. The prototype's [release-policy research](../../docs/withdrawal-policy.md)
describes the Praxis inspiration and the other deployment gates.
