import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { readFile, stat, writeFile } from "node:fs/promises"
import { join } from "node:path"
import { AccountBlockTemplate, EmbeddedContract, Hash, SETTLEMENT_ADDRESS, ZNN_ZTS, settlement, type LedgerApi } from "znn-typescript-sdk"
import { CHAIN_ID, CONFIRMATION_DEPTH, DOMAIN_ID, PILLAR_NAME, PILLAR_URL, WITHDRAWAL_DELAY,
  connect, keyPair, sendAndWait, waitFor, waitHeight } from "../src/devnet.js"
import { compose } from "./lib.js"

const BATCH = 4n
const GUEST_KEY = "0x00e4e8f52c5c085d833abcfacbbf0270e9832cd2af090ae12f7659c08270ffa4"
const VK_SHA256 = "b3e14fe98042c3ff6f219172965bddd8dda586c556adac9b4acbd285e13d3cdb"
const ANCHOR_KEY = Buffer.from("f100000001", "hex")
const KEYS = settlement.SettlementKeys

class SubmitBatchContract extends EmbeddedContract {
  protected static readonly definition = JSON.stringify([{
    type: "function", name: "SubmitBatch", inputs: [{ name: "commitment", type: "bytes" }],
  }])
}

interface Pending {
  schema: number
  chainId: number
  domain: number
  batch: number
  genesis: string
  height: number
  hash: string
  root: string
  submitter: string
  sends: { hash: string; receiveHeight: number }[]
}

interface Proposal {
  commitment: string
  firstOrdinal: number
  inputs: { Ctx: { GlobalInputIndex: number; MomentumHeight: number; MomentumHash: string } }[]
}

async function readJson<T>(path: string, limit: number): Promise<T> {
  assert.ok((await stat(path)).size <= limit, "input exceeds its byte limit")
  return JSON.parse(await readFile(path, "utf8")) as T
}

async function checkpoint(ledger: LedgerApi, height: number) {
  const headers = await ledger.getMomentumsByHeight(height, 1)
  const header = headers.list[0]
  assert.ok(header?.stateRoot && header.height === height && header.version === 3)
  assert.equal(header.chainIdentifier, CHAIN_ID)
  const response = await fetch(PILLAR_URL, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ledger.getMomentumsByHeight", params: [height, 1] }),
    signal: AbortSignal.timeout(8000),
  })
  assert.equal(response.status, 200)
  const text = await response.text()
  assert.ok(text.length <= 65_536)
  const producer = JSON.parse(text).result?.list?.[0]
  assert.ok(producer && producer.height === height && producer.chainIdentifier === CHAIN_ID && producer.version === 3)
  assert.equal(producer.hash, header.hash.toString())
  assert.equal(producer.stateRoot, header.stateRoot.toString())
  return { height, hash: header.hash.toString(), root: header.stateRoot.toString(), headers }
}

async function readState(ledger: LedgerApi, head: Awaited<ReturnType<typeof checkpoint>>, key: Uint8Array, variable?: string) {
  const treeKey = settlement.stateTreeKey(key)
  const raw = await ledger.getProof(head.height, treeKey)
  const bound = {
    getMomentumsByHeight: async () => head.headers,
    getProof: async () => raw,
  } as unknown as LedgerApi
  const storage = new settlement.SettlementStorage(bound)
  const checked = await storage.read(head.height, key)
  const decoded = variable && checked.present ? await storage.readVariable(head.height, key, variable) : null
  return { decoded, evidence: {
    key: Buffer.from(key).toString("hex"), treeKey: Buffer.from(treeKey).toString("hex"),
    value: checked.value === null ? null : Buffer.from(checked.value).toString("base64"),
    proof: Buffer.from(raw.proof).toString("base64"),
  } }
}

function hex(value: unknown): string {
  return value instanceof Uint8Array ? Buffer.from(value).toString("hex") : String(value).replace(/^0x/, "").toLowerCase()
}

function checkEntry(entry: Record<string, any>, height: number, commitment: Buffer) {
  assert.equal(BigInt(entry.protocolVersion), BigInt(commitment.readUInt16BE()))
  assert.equal(BigInt(entry.firstInputSeq), commitment.readBigUInt64BE(30))
  assert.equal(BigInt(entry.lastInputSeq), commitment.readBigUInt64BE(38))
  for (const [i, field] of ["preStateRoot", "postStateRoot", "inputRoot", "receiptRoot", "eventRoot", "outboxRoot", "daHash"].entries()) {
    assert.equal(hex(entry[field]), commitment.subarray(46 + 32*i, 78 + 32*i).toString("hex"), field)
  }
  assert.equal(BigInt(entry.daMode), BigInt(commitment[270]))
  assert.equal(entry.executorPillar, PILLAR_NAME)
  assert.equal(BigInt(entry.state), 0n)
  assert.equal(hex(entry.assetFlowSummary), commitment.subarray(299, -4).toString("hex"))
  assert.equal(hex(entry.proofData), "")
  const submitted = BigInt(entry.submittedAtHeight)
  assert.ok(submitted > 0n && submitted <= BigInt(height))
  assert.equal(BigInt(entry.finalizeAtHeight) - submitted, WITHDRAWAL_DELAY)
}

async function observe(ledger: LedgerApi, height: number, accepted: boolean, commitment: Buffer, chainEnd: string) {
  const head = await checkpoint(ledger, height)
  const [batch, cursor, anchor] = await Promise.all([
    readState(ledger, head, KEYS.batchEntry(DOMAIN_ID, BATCH), "batchEntry"),
    readState(ledger, head, KEYS.inputSequenceCursor(DOMAIN_ID), "inputSequenceCursor"),
    readState(ledger, head, ANCHOR_KEY),
  ])
  assert.ok(cursor.decoded, "missing proven cursor")
  const next = BigInt(cursor.decoded.nextBatchId)
  const consumed = BigInt(cursor.decoded.consumedInputCount)
  assert.equal(next, accepted ? 5n : 4n)
  assert.equal(consumed, accepted ? 7n : 4n)
  if (accepted) {
    assert.ok(batch.decoded && anchor.evidence.value, "verified batch and chain anchor must exist")
    checkEntry(batch.decoded, height, commitment)
    const expected = Buffer.alloc(48)
    expected.writeBigUInt64BE(next)
    expected.writeBigUInt64BE(consumed, 8)
    Buffer.from(chainEnd, "hex").copy(expected, 16)
    assert.equal(anchor.evidence.value, expected.toString("base64"))
  } else {
    assert.equal(batch.evidence.value, null)
    assert.equal(anchor.evidence.value, null)
  }
  return {
    height, hash: head.hash, root: head.root, nextBatch: Number(next), consumed: Number(consumed),
    batch: batch.evidence, cursor: cursor.evidence, anchor: anchor.evidence,
  }
}

async function pendingInputs(ledger: LedgerApi, head: Awaited<ReturnType<typeof checkpoint>>, pending: Pending, proposal: Proposal) {
  const count = await readState(ledger, head, KEYS.domainInputCounter(DOMAIN_ID), "domainInputCounter")
  assert.ok(count.decoded)
  assert.equal(BigInt(count.decoded.count), 7n)
  const ordinals = []
  for (const [i, send] of pending.sends.entries()) {
    const { Ctx } = proposal.inputs[i]
    const ordinal = await readState(ledger, head, KEYS.domainInputOrdinal(DOMAIN_ID, BigInt(proposal.firstOrdinal + i)), "domainInputOrdinal")
    assert.ok(ordinal.decoded)
    assert.equal(BigInt(ordinal.decoded.globalInputIndex), BigInt(Ctx.GlobalInputIndex))
    assert.equal(hex(ordinal.decoded.sourceAccountBlockHash), send.hash)
    const block = await ledger.getAccountBlockByHash(Hash.parse(send.hash))
    const receive = block?.pairedAccountBlock
    assert.ok(receive?.hash && block?.confirmationDetail)
    assert.equal(block.confirmationDetail.momentumHeight, Ctx.MomentumHeight)
    const source = (await ledger.getMomentumsByHeight(Ctx.MomentumHeight, 1)).list[0]
    assert.equal(source?.hash.toString(), Ctx.MomentumHash)
    const confirmed = await ledger.getAccountBlockByHash(receive.hash)
    assert.ok(confirmed?.confirmationDetail)
    assert.equal(confirmed.confirmationDetail.momentumHeight, send.receiveHeight)
    assert.equal(Buffer.from(confirmed.data).readBigUInt64BE(), 1n)
    ordinals.push(ordinal.evidence)
  }
  return { count: count.evidence, ordinals }
}

function withProof(commitment: Buffer, proof: Buffer): Buffer {
  const length = Buffer.alloc(4)
  length.writeUInt32BE(proof.length)
  return Buffer.concat([commitment.subarray(0, -4), length, proof])
}

async function main(mode: string, work: string) {
  assert.ok(mode === "preflight" || mode === "submit")
  const pending = await readJson<Pending>(join(work, "pending.json"), 8192)
  const proposal = await readJson<Proposal>(join(work, "proposal.json"), 49_152)
  const statement = await readJson<{ journal: string; chainEnd: string }>(join(work, "statement.json"), 2048)
  assert.equal(pending.schema, 1)
  assert.equal(pending.chainId, CHAIN_ID)
  assert.equal(pending.domain, DOMAIN_ID)
  assert.equal(BigInt(pending.batch), BATCH)
  assert.equal(proposal.firstOrdinal, 4)
  assert.equal(pending.sends.length, 3)
  assert.equal(proposal.inputs.length, 3)
  assert.match(statement.journal, /^[0-9a-f]{64}$/)
  assert.match(statement.chainEnd, /^[0-9a-f]{64}$/)
  const commitment = Buffer.from(proposal.commitment, "base64")
  assert.equal(commitment.length, 307)
  assert.equal(commitment.readUInt32BE(299), 0, "expected zero asset flow")
  assert.equal(commitment.readUInt32BE(303), 0, "expected empty proof field in the journal")
  const executor = keyPair("executor")
  assert.equal(executor.address.toString(), pending.submitter)
  assert.deepEqual(commitment.subarray(271, 291), Buffer.from(executor.address.getBytes()))

  const zenon = await connect()
  const ledger = zenon.ledger
  const genesis = (await ledger.getMomentumsByHeight(1, 1)).list[0]
  assert.ok(genesis?.chainIdentifier === CHAIN_ID)
  assert.equal(genesis.hash.toString(), pending.genesis)
  const frozen = await checkpoint(ledger, pending.height)
  assert.equal(frozen.hash, pending.hash)
  assert.equal(frozen.root, pending.root)
  const admitted = await pendingInputs(ledger, frozen, pending, proposal)
  const before = await observe(ledger, pending.height, false, commitment, statement.chainEnd)
  const live = await waitFor("the resumed devnet to extend the checkpoint", 150_000, async () => {
    const front = await ledger.getFrontierMomentum()
    return front.height > pending.height ? front : undefined
  })
  const resumed = await observe(ledger, live.height, false, commitment, statement.chainEnd)
  console.log(`VALIDITY_RESTORED ${frozen.height} ${frozen.hash} ${resumed.height} ${resumed.hash}`)
  if (mode === "preflight") return

  const fixture = await readJson<{ schema: string; version: string; verificationKeySha256: string; guestKey: string; journal: string; proof: string }>(join(work, "fixture.json"), 8192)
  assert.equal(fixture.schema, "zeno-settlement-r4-groth16-v1")
  assert.equal(fixture.version, "v2.0.0")
  assert.equal(fixture.verificationKeySha256, VK_SHA256)
  assert.equal(fixture.guestKey, GUEST_KEY)
  assert.equal(fixture.journal, statement.journal)
  assert.match(fixture.proof, /^[0-9a-f]{512}$/)
  const proof = Buffer.from(fixture.proof, "hex")
  const data = SubmitBatchContract.encodeCall("SubmitBatch", [withProof(commitment, proof)])
  const transaction = AccountBlockTemplate.callContract(SETTLEMENT_ADDRESS, ZNN_ZTS, 0n, data)
  assert.ok(transaction.data.length < 16_384)
  const sent = await sendAndWait(executor, transaction)
  assert.ok(sent.accepted, "the proof-bearing SubmitBatch failed")
  await waitHeight(sent.receiveHeight + CONFIRMATION_DEPTH)
  const included = await ledger.getAccountBlockByHash(sent.hash)
  const receive = included?.pairedAccountBlock
  assert.ok(included && receive?.hash && receive.data.length === 8)
  assert.equal(included.address.toString(), pending.submitter)
  assert.deepEqual(Buffer.from(included.data), Buffer.from(transaction.data))
  const receiveCode = Number(Buffer.from(receive.data).readBigUInt64BE())
  assert.equal(receiveCode, 1)
  const observed = await observe(ledger, sent.receiveHeight, true, commitment, statement.chainEnd)
  const accepted = {
    send: sent.hash.toString(), receive: receive.hash.toString(), receiveCode,
    dataBytes: transaction.data.length, basePlasma: included.basePlasma, usedPlasma: included.usedPlasma, ...observed,
  }
  const result = JSON.stringify({
    schema: 1, chainId: CHAIN_ID, domain: DOMAIN_ID, batch: Number(BATCH), genesis: pending.genesis,
    journal: statement.journal, guestKey: GUEST_KEY, chainEnd: statement.chainEnd, commitment: proposal.commitment,
    proofSha256: createHash("sha256").update(proof).digest("hex"), inputSends: pending.sends,
    admitted, before, resumed, accepted,
  }) + "\n"
  assert.ok(Buffer.byteLength(result) <= 96 * 1024)
  await writeFile(join(work, "acceptance.json"), result, { flag: "wx", mode: 0o600 })
  console.log(`VALIDITY_SUBMIT ${accepted.receiveCode} ${accepted.height} ${accepted.nextBatch} ${accepted.consumed}`)
}

const [mode, work] = process.argv.slice(2)
if (mode === "build-executor") {
  await compose("build", "executor", "gateway")
} else if (mode === "start-executor") {
  await compose("up", "-d", "--no-build", "--pull", "never", "executor", "gateway")
} else if (mode === "stop-devnet") {
  await compose("down")
} else {
  await main(mode, work)
}
