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
