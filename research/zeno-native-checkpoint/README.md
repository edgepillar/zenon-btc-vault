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

The [original workflow](https://github.com/edgepillar/zenon-btc-vault/blob/5eb82c39991fa79568d9534e0f73bdc39fc9e469/.github/workflows/zeno-native-checkpoint.yml) restores
the public database checkpoint from PR 13 and submits the associated validity
proof. Its source run, artifact digests and raw input digests are pinned. It
then runs the native selector and retains the state and checkpoint records.
The first run selected eligible Momentum 151 and verified the 31 signed
headers through Momentum 181 on both nodes. Both nodes refused the early
request, and an offline control refused a conflicting view at the same
height. This run used PR 13's demo authorization digest.
[Original batch proof](https://github.com/edgepillar/zenon-btc-vault/pull/13) ·
[Run summary](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46114229782#summary-140884312846) ·
[Checkpoint record](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46114229782/artifacts/15251645707)

The workflow also retains the v3 hash preimages, public keys, signatures and
both sets of Settlement paths in the version 2 checkpoint record. A
separate runner binds that record to the capture job and verifies the
signed Momentum chain and all six cut state paths using the archived batch
evidence. Its controls refuse an altered signature and an altered batch
leaf. Producer election is checked by the live nodes against their parent
state.

The [Zeno checks workflow](../../.github/workflows/zeno-checks.yml) replays
the [committed fixture](fixture) from the signed capture at `5eb82c3`.
It verifies 31 signatures and six state paths over Momentum 151 through 181.
[Original signed capture and replay](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46117123125)

The [regtest release workflow](../../.github/workflows/zeno-release-e2e.yml)
uses the same selector on a new batch whose authorization binds a registered
Bitcoin transaction. It rechecks the Bitcoin tip, refund window and
registration after the native check and before publishing the signed bytes to
Bitcoin Core regtest. The first integrated run accepted at Momentum 130,
selected eligible Momentum 159, verified the segment through Momentum 189
and confirmed the 999,000-satoshi output in Bitcoin regtest block 103.
[Original integrated run](https://github.com/edgepillar/zenon-btc-vault/actions/runs/46115346873#summary-140887845505)

The policy trusts the configured local full nodes, their devnet genesis and
the pinned implementation. The 30 successors protect the selected eligible
header under the pinned rollback rule. The cut root records the state both
nodes observed at the time of release. A live release policy needs
authenticated node ownership, synchronization guarantees and explicit
reconciliation of Bitcoin confirmation and refund. Mainnet consensus
finality remains open.
