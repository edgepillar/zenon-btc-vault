import assert from "node:assert/strict"
import { readFile, stat, writeFile } from "node:fs/promises"
import { prepareCall } from "zeno-client"
import { Hash, settlement, type LedgerApi } from "znn-typescript-sdk"
import { CHAIN_ID, DOMAIN_ID, keyPair } from "../src/devnet.js"
import {
  BALANCE_LEAF_BYTES, ONE_ZNN, PLASMA_LIMIT, ZNN_ASSET, buyStorage, depositTo,
  deployVault, expectStatus, flow, send, track, user,
} from "./lib.js"

const MAX_EVIDENCE_BYTES = 32_768

interface Checkpoint {
  chainIdentifier: number
  version: number
  height: number
  hash: string
  root: string
}

interface Claim {
  domainId: number
  batchId: number
  outboxId: string
  sourceContract: string
  recipient: string
  assetId: string
  amount: string
}

interface Snapshot {
  checkpoint: Checkpoint
  stateProof: { value: string; proof: string; root: string }
}

interface Evidence {
  schemaVersion: 1
  sendHash: string
  claim: Claim
  outbox: { message: string; proof: string }
  finalized: Snapshot
  beforeFinalization: Snapshot
}

function batchKey(claim: Claim): Uint8Array {
  return settlement.SettlementKeys.batchEntry(claim.domainId, BigInt(claim.batchId))
}

async function observe(ledger: LedgerApi, claim: Claim, height: number): Promise<Snapshot> {
  const header = (await ledger.getMomentumsByHeight(height, 1)).list[0]
  assert.ok(header?.stateRoot && header.height === height && header.version === 3, "expected a v3 checkpoint")
  assert.equal(header.chainIdentifier, CHAIN_ID, "expected the pinned devnet chain")
  const proof = await ledger.getProof(height, settlement.stateTreeKey(batchKey(claim)))
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

async function verify(snapshot: Snapshot, checkpoint: Checkpoint, claim: Claim, outbox: Evidence["outbox"]) {
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
    checkpoint.height, batchKey(claim), "batchEntry",
  )
  assert.ok(entry, "batch must be present")
  assert.equal(BigInt(entry.protocolVersion), 1n, "unexpected Settlement protocol version")
  assert.equal(BigInt(entry.state), 0n, "unexpected Settlement acceptance state")
  const finalizeAt = BigInt(entry.finalizeAtHeight)
  assert.ok(finalizeAt >= BigInt(entry.submittedAtHeight) && finalizeAt > 0n, "invalid Settlement finalization height")
  assert.ok(BigInt(checkpoint.height) >= finalizeAt, "checkpoint is before the batch finalizes")

  const message = Buffer.from(outbox.message, "hex")
  const proof = Buffer.from(outbox.proof, "hex")
  const item = settlement.decodeOutboxWithdrawal(message)
  assert.equal(
    settlement.outboxProofRoot(message, proof).toString(), entry.outboxRoot.toString(),
    "outbox root must match the authenticated batch",
  )
  assert.equal(
    settlement.outboxId(claim.domainId, BigInt(claim.batchId), item.inputIndex, item.outboxIndex).toString(),
    claim.outboxId, "unexpected outbox identity",
  )
  assert.equal(item.sourceContract.toString(), claim.sourceContract, "unexpected source contract")
  assert.equal(item.assetId.toString(), claim.assetId, "unexpected withdrawal asset")
  assert.equal(item.recipient.toString(), claim.recipient, "unexpected withdrawal recipient")
  assert.equal(item.amount.toString(), claim.amount, "unexpected withdrawal amount")
  return Number(finalizeAt)
}

async function checkEvidence(evidence: Evidence) {
  assert.equal(evidence.schemaVersion, 1, "unexpected evidence schema version")
  const { claim, outbox, finalized, beforeFinalization } = evidence
  assert.equal(claim.domainId, DOMAIN_ID, "unexpected devnet domain")
  assert.equal(claim.assetId, ZNN_ASSET.toString(), "unexpected devnet asset")
  assert.equal(claim.recipient, keyPair("recipient").address.toString(), "unexpected devnet recipient")
  assert.equal(claim.amount, ((2n * ONE_ZNN) / 5n).toString(), "unexpected devnet amount")
  const finalizeAt = await verify(finalized, finalized.checkpoint, claim, outbox)
  assert.equal(beforeFinalization.checkpoint.height, finalizeAt - 1, "expected the last pre-finalization height")
  await assert.rejects(verify(beforeFinalization, beforeFinalization.checkpoint, claim, outbox), /before the batch finalizes/)
  await assert.rejects(verify(finalized, beforeFinalization.checkpoint, claim, outbox), /selected devnet checkpoint/)
  await assert.rejects(verify({
    ...finalized, stateProof: { ...finalized.stateProof, root: "01".repeat(32) },
  }, finalized.checkpoint, claim, outbox), /proof root does not match/)
  await assert.rejects(verify(finalized, finalized.checkpoint, { ...claim, domainId: claim.domainId + 1 }, outbox), /different path/)
  await assert.rejects(verify(finalized, finalized.checkpoint, { ...claim, outboxId: "01".repeat(32) }, outbox), /unexpected outbox identity/)
  await assert.rejects(verify(finalized, finalized.checkpoint, { ...claim, amount: "1" }, outbox), /unexpected withdrawal amount/)
  console.log("PASS: verified devnet batch state and withdrawal under the selected v3 checkpoint")
  console.log("PASS: refused an early batch, another checkpoint, a wrong root, domain, outbox ID and amount")
}

async function replay(file: string) {
  assert.ok((await stat(file)).size <= MAX_EVIDENCE_BYTES, "evidence exceeds its byte limit")
  await checkEvidence(JSON.parse(await readFile(file, "utf8")))
}

if (process.argv[2] === "--verify") {
  await replay(process.argv[3])
} else {
  flow("vault-withdrawal-evidence", async (ctx) => {
    const file = process.env.ZENO_EVIDENCE_FILE
    assert.ok(file, "set ZENO_EVIDENCE_FILE to a fresh output file")
    const recipient = keyPair("recipient").address.toString()
    const amount = (2n * ONE_ZNN) / 5n
    const vault = await deployVault(ctx, "vault")
    await buyStorage(ctx, vault, BALANCE_LEAF_BYTES)
    await depositTo(ctx, "deposit sent", vault.contractId, ONE_ZNN, (r) => expectStatus(r, "SUCCESS"))
    const tx = ctx.zenon.embedded.settlement.callL2(
      DOMAIN_ID, vault.contractId, PLASMA_LIMIT,
      prepareCall(vault.iface, "withdraw", [ZNN_ASSET.getBytes(), amount, recipient]),
    )
    const { hash } = await send(user, tx)
    const withdrawal = track(ctx, "withdrawal", hash, (r) => {
      expectStatus(r, "SUCCESS")
      assert.equal(r.outbox.length, 1, "expected one withdrawal outbox message")
    })
    const accepted = await withdrawal.at("latest")
    const receipt = await withdrawal.at("finalized")
    assert.equal(receipt.batchId, accepted.batchId, "finalization must refer to the accepted batch")
    const item = receipt.outbox[0]
    const proof = await ctx.gateway.getOutboxProof(item.outboxId)
    assert.equal(proof.batchId, receipt.batchId, "withdrawal must belong to the accepted batch")
    const claim: Claim = {
      domainId: DOMAIN_ID,
      batchId: proof.batchId,
      outboxId: item.outboxId,
      sourceContract: vault.contractId.toString(),
      recipient,
      assetId: ZNN_ASSET.toString(),
      amount: amount.toString(),
    }
    const outbox = { message: proof.message, proof: proof.inclusionProof }
    const ledger = ctx.zenon.ledger
    const finalized = await observe(ledger, claim, (await ledger.getFrontierMomentum()).height)
    const batch = await ctx.zenon.embedded.settlement.getBatch(DOMAIN_ID, proof.batchId)
    // The RPC height selects the earlier sample. Replay proves the deadline from storage.
    assert.ok(batch?.isFinalized && Number.isSafeInteger(batch.finalizeAtHeight) && batch.finalizeAtHeight > 1)
    const beforeFinalization = await observe(ledger, claim, batch.finalizeAtHeight - 1)
    const evidence: Evidence = {
      schemaVersion: 1, sendHash: hash.toString(), claim, outbox, finalized, beforeFinalization,
    }
    const serialized = JSON.stringify(evidence) + "\n"
    assert.ok(Buffer.byteLength(serialized) <= MAX_EVIDENCE_BYTES, "evidence exceeds its byte limit")
    await writeFile(file, serialized, { flag: "wx", mode: 0o600 })
  })
}
