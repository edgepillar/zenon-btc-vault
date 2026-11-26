import assert from "node:assert/strict"
import { readFile, stat, writeFile } from "node:fs/promises"
import { prepareCall } from "zeno-client"
import { settlement } from "znn-typescript-sdk"
import { DOMAIN_ID, keyPair } from "../src/devnet.js"
import { observeBatch, proveBatch, type Checkpoint, type Snapshot } from "./batch-proof.js"
import {
  BALANCE_LEAF_BYTES, ONE_ZNN, PLASMA_LIMIT, ZNN_ASSET, buyStorage, depositTo,
  deployVault, expectStatus, flow, send, track, user,
} from "./lib.js"

const MAX_EVIDENCE_BYTES = 32_768

interface Claim {
  domainId: number
  batchId: number
  outboxId: string
  sourceContract: string
  recipient: string
  assetId: string
  amount: string
}

interface Evidence {
  schemaVersion: 1
  sendHash: string
  claim: Claim
  outbox: { message: string; proof: string }
  finalized: Snapshot
  beforeFinalization: Snapshot
}

async function verify(snapshot: Snapshot, checkpoint: Checkpoint, claim: Claim, outbox: Evidence["outbox"]) {
  const entry = await proveBatch(snapshot, checkpoint, claim.domainId, claim.batchId)

  const message = Buffer.from(outbox.message, "hex")
  const proof = Buffer.from(outbox.proof, "hex")
  const item = settlement.decodeOutboxWithdrawal(message)
  const committedOutboxRoot = String(entry.outboxRoot)
  assert.match(committedOutboxRoot, /^0x[0-9a-fA-F]{64}$/, "expected a 32-byte ABI hash")
  assert.equal(
    settlement.outboxProofRoot(message, proof).toString(), committedOutboxRoot.slice(2).toLowerCase(),
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
  return Number(entry.finalizeAtHeight)
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
    const finalized = await observeBatch(ledger, claim.domainId, claim.batchId, (await ledger.getFrontierMomentum()).height)
    const batch = await ctx.zenon.embedded.settlement.getBatch(DOMAIN_ID, proof.batchId)
    // The RPC height selects the earlier sample. Replay proves the deadline from storage.
    assert.ok(batch?.isFinalized && Number.isSafeInteger(batch.finalizeAtHeight) && batch.finalizeAtHeight > 1)
    const beforeFinalization = await observeBatch(ledger, claim.domainId, claim.batchId, batch.finalizeAtHeight - 1)
    const evidence: Evidence = {
      schemaVersion: 1, sendHash: hash.toString(), claim, outbox, finalized, beforeFinalization,
    }
    const serialized = JSON.stringify(evidence) + "\n"
    assert.ok(Buffer.byteLength(serialized) <= MAX_EVIDENCE_BYTES, "evidence exceeds its byte limit")
    await writeFile(file, serialized, { flag: "wx", mode: 0o600 })
  })
}
