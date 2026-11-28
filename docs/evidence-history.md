# Workflow consolidation and evidence history

The three active workflows cover [Bitcoin regtest](../.github/workflows/regtest.yml),
[Zeno evidence and Settlement checks](../.github/workflows/zeno-checks.yml), and the
[end-to-end release](../.github/workflows/zeno-release-e2e.yml). The end-to-end run
builds its proving tools from pinned source into empty caches. Each proof stage
uses output from the current workflow run and checks its digest before use.

The [pre-consolidation tree](https://github.com/edgepillar/zenon-btc-vault/tree/5eb82c39991fa79568d9534e0f73bdc39fc9e469/.github/workflows)
preserves the experiment workflows with their original retention periods.
Their downloadable artifacts remain subject to the original expiration dates.
The recorded outcomes and source identities below also apply after expiration.

| Experiment | Retained evidence |
| --- | --- |
| Bitcoin registration, fees and competing spends | [PR #1](https://github.com/edgepillar/zenon-btc-vault/pull/1), [PR #2](https://github.com/edgepillar/zenon-btc-vault/pull/2), [PR #3](https://github.com/edgepillar/zenon-btc-vault/pull/3) |
| Devnet withdrawal and Bitcoin payout binding | [Captured withdrawal](../research/zeno-withdrawal/README.md), [Withdrawal authorization](withdrawal-authorization.md), [Contract authorization](../research/zeno-contract-authorization/README.md) |
| Native execution replay and Groth16 authorization | [Execution replay](../research/zeno-execution-replay/README.md), [Authorization proof](../research/authorization-proof/README.md) |
| Full batch proof, Settlement receive and input-chain journal | [Full batch](../research/zeno-batch-proof/README.md), [Settlement proof](../research/zeno-settlement-proof/README.md), [Validity statement](../research/zeno-validity-statement/README.md) |
| Fresh proof-bearing SubmitBatch and registered release | [Live validity](../research/zeno-validity-devnet/README.md), [Release evidence audit](../research/zeno-release-audit/README.md) |
| Native checkpoint and offline signature replay | [Native checkpoint](../research/zeno-native-checkpoint/README.md) |
| Mainnet header sample | [Observation and scope](../research/mainnet-readiness/README.md), [Original workflow](https://github.com/edgepillar/zenon-btc-vault/blob/066b29b30adbec3aaf62b9497869545752bfa39f/.github/workflows/mainnet-readiness.yml) |

## Historical proving tool archives

| Artifact | Source revision | Archive SHA-256 |
| --- | --- | --- |
| [Core prover](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45983864343/artifacts/15191636225) | `97175d393548b23d1afbbc5e127159b7d943f362` | `da3a368f90bca1b51c8fa398937d28ba4b38217280229948ba7b7a2256814edb` |
| [Compressor](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46026102217/artifacts/15212891808) | `7f4591481b2c9ea6b35afccc8b895ea0e4f20942` | `7340ed78fc477a53c77be7b809e1106bb33a0427ad79bdb320b617c74cf46dea` |
| [Wrapper](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46026102217/artifacts/15212787070) | `7f4591481b2c9ea6b35afccc8b895ea0e4f20942` | `9a7610bad71a333f3eba4a020438a6e8dde7184e2b68baba20bf99d013a6e25d` |

In run `45983864343`, attempt 1, the statement job built and checked the Core
prover. The later proof job lost runner communication and the attempt ended
in failure. The preserved tool belongs to the successful statement job.
In run `46026102217`, attempt 1, the tools job built and checked both binaries.
[Statement job](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45983864343/job/140454345462) ·
[Proof attempt](https://github.com/edgepillar/zenon-btc-vault/actions/runs/45983864343/job/140458911997) ·
[Tools job](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46026102217/job/140597785715)
