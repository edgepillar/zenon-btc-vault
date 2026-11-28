import assert from "node:assert/strict"
import { Hash, settlement, type LedgerApi } from "znn-typescript-sdk"
import { CHAIN_ID } from "../src/devnet.js"

export interface Checkpoint {
  chainIdentifier: number
  version: number
  height: number
  hash: string
  root: string
}

export interface Snapshot {
  checkpoint: Checkpoint
  stateProof: { value: string; proof: string; root: string }
}

export async function observeBatch(ledger: LedgerApi, domainId: number, batchId: number, height: number): Promise<Snapshot> {
  const header = (await ledger.getMomentumsByHeight(height, 1)).list[0]
  assert.ok(header?.stateRoot && header.height === height && header.version === 3, "expected a v3 checkpoint")
  assert.equal(header.chainIdentifier, CHAIN_ID, "expected the pinned devnet chain")
  const key = settlement.SettlementKeys.batchEntry(domainId, BigInt(batchId))
  const proof = await ledger.getProof(height, settlement.stateTreeKey(key))
  assert.ok(proof.value, "batch state proof is missing")
  const currentHeader = (await ledger.getMomentumsByHeight(height, 1)).list[0]
  assert.equal(currentHeader?.hash.toString(), header.hash.toString(), "the checkpoint changed during capture")
  return {
    checkpoint: {
      chainIdentifier: header.chainIdentifier,
      version: header.version,
      height,
      hash: header.hash.toString(),
      root: header.stateRoot.toString(),
    },
    stateProof: {
      value: Buffer.from(proof.value).toString("base64"),
      proof: Buffer.from(proof.proof).toString("base64"),
      root: proof.root.toString(),
    },
  }
}

export async function proveBatch(snapshot: Snapshot, checkpoint: Checkpoint, domainId: number, batchId: number) {
  assert.deepEqual(snapshot.checkpoint, checkpoint, "evidence must match the selected devnet checkpoint")
  assert.equal(checkpoint.chainIdentifier, CHAIN_ID, "unexpected chain")
  assert.equal(checkpoint.version, 3, "unexpected Momentum version")
  assert.ok(Number.isSafeInteger(checkpoint.height) && checkpoint.height > 0, "invalid checkpoint height")

  const { stateProof } = snapshot
  // Replay the captured RPC values through the pinned SDK's local SMT verifier.
  const ledger = {
    getMomentumsByHeight: async () => ({
      list: [{ height: checkpoint.height, stateRoot: Hash.parse(checkpoint.root) }],
    }),
    getProof: async () => ({
      value: Buffer.from(stateProof.value, "base64"),
      proof: Buffer.from(stateProof.proof, "base64"),
      root: Hash.parse(stateProof.root),
    }),
  } as unknown as LedgerApi
  const entry = await new settlement.SettlementStorage(ledger).readVariable(
    checkpoint.height, settlement.SettlementKeys.batchEntry(domainId, BigInt(batchId)), "batchEntry",
  )
  assert.ok(entry, "batch must be present")
  assert.equal(BigInt(entry.protocolVersion), 1n, "unexpected Settlement protocol version")
  assert.equal(BigInt(entry.state), 0n, "unexpected Settlement acceptance state")
  const finalizeAt = BigInt(entry.finalizeAtHeight)
  assert.ok(finalizeAt >= BigInt(entry.submittedAtHeight) && finalizeAt > 0n, "invalid Settlement finalization height")
  assert.ok(BigInt(checkpoint.height) >= finalizeAt, "checkpoint is before the batch finalizes")
  return entry
}
