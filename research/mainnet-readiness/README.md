# Zenon mainnet proof readiness

The read-only probe checks whether two documented public nodes return the
expected Zenon mainnet genesis and agree on two adjacent Momentum headers.
It selects the last pair at or below the lower observed frontier. The
prospective Bitcoin release remains `NO_GO`.

The probe uses the [pinned SPV fetch implementation](https://github.com/edgepillar/zenon-spv/blob/f058474d5f35cfa08cdccbf153d6fb9fbf9ab649/internal/fetch/momentum.go)
to recompute the hashes from the RPC payloads. It compares the height-1
headers against the SPV genesis pin and checks both sample hashes, claimed
signer keys, signatures, parent link and chain identifier. The two endpoint
hosts are listed in the [public node registry](https://github.com/zenon-network/zenon-node-database/blob/a4a368c6320047c4cf1ac751b6b0fa8cb2471b48/rpc-nodes-mainnet.json).
The published HTTPS listeners are documented by
[HyperCore](https://forum.hypercore.one/t/hypercore-public-rpc-nodes/119)
and [Zenon Hub](https://zenonhub.io/services/public-nodes).

The capture contains selected header fields and commitments. Offline replay
checks its SHA-256 before rehashing the retained header fields and checking the
signatures, link and comparison. The live capture is the step that recomputes
the data and content commitments from their RPC preimages.

## Recorded mainnet observation

The [hosted capture](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45836789285)
ran on Linux amd64 with Go 1.26.0 at
`6a2e21fab3f3efec3fe24dfcff206dafd0e41e88`. Both public nodes returned the
same genesis for chain ID 1 and the same two signed v1 headers.

| Height | Observed time in UTC | Momentum hash |
| --- | --- | --- |
| 1 | 2021-11-24 12:00:00 | `9e204601d1b7b1427fe12bc82622e610d8a6ad43c40abf020eb66e538bb8eeb0` |
| 14,732,845 | 2026-11-26 14:13:30 | `a6c7ff4b9310d8e3f0e6c2032ffde165d634fcdc7d398038d1b6a7cb52482dd4` |
| 14,732,846 | 2026-11-26 14:13:50 | `bc5f3ea570f78f4737f207e3cc405bfac598e7951b466e87aeb07f7cc198e684` |

The [5,299-byte capture](evidence.json) completed at 14:13:53 UTC and is
pinned by [SHA-256](evidence.sha256). The sampled layouts have no
authenticated `StateRoot` field. A successful job means the observation or
replay completed. The assessment retains `bitcoinRelease: NO_GO`.

The [Zeno checks workflow](../../.github/workflows/zeno-checks.yml) builds the
probe from the pinned SPV source and replays this capture. The
[original workflow](https://github.com/edgepillar/zenon-btc-vault/blob/066b29b30adbec3aaf62b9497869545752bfa39f/.github/workflows/mainnet-readiness.yml)
records the manual fresh-observation procedure.

## Requirements for a mainnet withdrawal proof

| Requirement | Evidence this probe can establish |
| --- | --- |
| Network identity | The two peers return a height-1 envelope whose locally recomputed hash matches the fixed mainnet genesis. |
| Sample agreement | The two peers return the same linked Momentum pair with valid signatures for the declared keys. |
| Authenticated L1 v3 activation | `NOT_ESTABLISHED`. The supported v1/v2 sample layouts carry no state root. An unsupported layout produces an incomplete observation. |
| Canonicality and finality | `NOT_ESTABLISHED`. A sample and its genesis observation leave history, producer elections and fork choice unverified. |
| Correct L2 execution | `NOT_ESTABLISHED`. The isolated Zeno Phase 1 experiment uses an executor attestation. |

The [released node verifier](https://github.com/zenon-network/go-zenon/blob/e8986a63f164259053e0342ceaf9a68cb85d649/verifier/momentum.go#L108-L116)
at `v0.0.10-alphanet` accepts v1. The
[development node verifier](https://github.com/zenon-network/go-zenon/blob/b85ee9459102363240dedffa3a2d017a7001e5c3/verifier/momentum.go#L146-L159)
selects v1 or v2 using its Dynamic Plasma spork. The
[Zeno devnet node](https://github.com/digitalSloth/go-zenon/blob/2835c436b197fece5c76adf4ba8dc1decca2e3ea/verifier/momentum.go#L149-L166)
contains the separate v3 state-root rule. A source snapshot provides the
rules of that revision. The network acceptance work requires an authenticated
activation profile, a contiguous chain anchored under the accepted network
policy, and an independently justified finality rule. The
[SPV integration contract](https://github.com/edgepillar/zenon-spv/blob/f058474d5f35cfa08cdccbf153d6fb9fbf9ab649/docs/verification-contract.md)
describes the boundaries of the current native verifier.
