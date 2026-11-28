# Native checkpoint selection for the devnet vault

This experiment selects a v3 Momentum on two nodes running
[the pinned devnet node](https://github.com/digitalSloth/go-zenon/tree/2835c436b197fece5c76adf4ba8dc1decca2e3ea).
Settlement supplies the withdrawal deadline through the proven batch entry.
The native selector reads the local canonical chain under the insertion lock
and verifies the checkpoint and its 30 successors. For each header, it
recomputes the Momentum hash and signature check and evaluates the elected
producer against the parent state.

The successor requirement follows the pinned side-branch insertion rule. A
frontier 30 blocks above the chosen Momentum is 31 blocks above its parent.
Replacing that Momentum through normal side-branch insertion would therefore
exceed the 30-block rollback bound. The devnet endpoint bounds historical
replay to 60 blocks below the frontier. [Native branch selection](https://github.com/digitalSloth/go-zenon/blob/2835c436b197fece5c76adf4ba8dc1decca2e3ea/protocol/chain_bridge.go#L207-L234)

The client requires the same eligible checkpoint and linked segment from both
nodes. Under the state root at the end of that segment, it independently
verifies that the batch entry, cursor and input-chain anchor match the eligible
view on both nodes. Its controls require refusal of the early native view and
a disagreement at the same height.

## Qualification

The [workflow](../../.github/workflows/zeno-native-checkpoint.yml) restores
the public database checkpoint from PR 13 and submits the associated validity
proof. Its source run, artifact digests and raw input digests are pinned. It
then runs the native selector and retains the state and checkpoint records.
This lane uses PR 13's demo authorization digest. It qualifies the native
checkpoint for that batch. [Original batch proof](https://github.com/edgepillar/zenon-btc-vault/pull/13)

The policy trusts the configured local full nodes, their devnet genesis and
the pinned implementation. A live release policy needs authenticated node
ownership, synchronization guarantees and explicit reconciliation of Bitcoin
confirmation and refund. Mainnet consensus finality remains open.
