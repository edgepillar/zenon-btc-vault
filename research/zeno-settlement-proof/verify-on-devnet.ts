import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { readFile, stat, writeFile } from "node:fs/promises"
import { AccountBlockTemplate, EmbeddedContract, SETTLEMENT_ADDRESS, ZNN_ZTS, settlement } from "znn-typescript-sdk"
import { CHAIN_ID, CONFIRMATION_DEPTH, FUSE_QSR, PILLAR_URL, connect, keyPair, sendAndWait, waitFor, waitHeight } from "../src/devnet.js"

const JOURNAL = "583827c2db6bb87baff06a8492742da5e9c2327f7009b73b4538de2b99a2cfcc"
const VK_HASH = "b3e14fe98042c3ff6f219172965bddd8dda586c556adac9b4acbd285e13d3cdb"
const DOMAIN = 1
const BATCH = 4n
const RECEIPT_KEY = Buffer.from("f0000000010000000000000004", "hex")
const FIELD = 21888242871839275222246405745257275088696311157297823662689037894645226208583n

class RecordedBatchContract extends EmbeddedContract {
  protected static readonly definition = JSON.stringify([{
    type: "function",
    name: "VerifyRecordedBatch",
    inputs: [
      { name: "domainId", type: "uint32" },
      { name: "batchId", type: "uint64" },
      { name: "journal", type: "hash" },
      { name: "proof", type: "bytes" },
    ],
  }])
}

function changedProof(proof: Buffer): Buffer {
  const y = BigInt(`0x${proof.subarray(32, 64).toString("hex")}`)
  assert.ok(0n < y && y < FIELD, "invalid Groth16 A point ordinate")
  const changed = Buffer.from(proof)
  Buffer.from((FIELD - y).toString(16).padStart(64, "0"), "hex").copy(changed, 32)
  return changed
}

async function checkProducer(height: number, hash: string, root: string) {
  const response = await fetch(PILLAR_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ledger.getMomentumsByHeight", params: [height, 1] }),
    signal: AbortSignal.timeout(8000),
  })
  assert.equal(response.status, 200)
  const data = await response.text()
  assert.ok(data.length < 65536, "unexpected producer response size")
  const header = JSON.parse(data).result?.list?.[0]
  assert.ok(header, "producer does not have the selected Momentum")
  assert.equal(header.chainIdentifier, CHAIN_ID)
  assert.equal(header.version, 3)
  assert.equal(header.height, height)
  assert.equal(header.hash, hash)
  assert.equal(header.stateRoot, root)
}

async function main(fixturePath: string, guestKey: string, evidencePath: string) {
  assert.ok((await stat(fixturePath)).size <= 8192, "unexpected proof fixture size")
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"))
  assert.equal(fixture.schema, "zeno-settlement-r4-groth16-v1")
  assert.equal(fixture.version, "v2.0.0")
  assert.equal(fixture.journal, JOURNAL)
  assert.equal(fixture.verificationKeySha256, VK_HASH)
  assert.equal(fixture.guestKey, guestKey)
  assert.match(fixture.proof, /^[0-9a-f]{512}$/)
  const proof = Buffer.from(fixture.proof, "hex")
  const proofDigest = createHash("sha256").update(proof).digest()
  const expected = Buffer.concat([Buffer.from(JOURNAL, "hex"), proofDigest])

  const zenon = await connect()
  await waitFor("a version 3 devnet Momentum", 180_000, async () => {
    const header = await zenon.ledger.getFrontierMomentum()
    return header.version === 3 && header.chainIdentifier === CHAIN_ID && header.stateRoot ? header : undefined
  })
  const sender = keyPair("admin")
  const fuse = await sendAndWait(sender, zenon.embedded.plasma.fuse(sender.address, FUSE_QSR))
  assert.equal(fuse.accepted, true, "devnet plasma fusion failed")
  const storage = new settlement.SettlementStorage(zenon.ledger)
  const cases = [
    { label: "changed-journal", journal: Buffer.from(JOURNAL, "hex"), proof, accepted: false, present: false },
    { label: "changed-proof", journal: Buffer.from(JOURNAL, "hex"), proof: changedProof(proof), accepted: false, present: false },
    { label: "original-proof", journal: Buffer.from(JOURNAL, "hex"), proof, accepted: true, present: true },
    { label: "repeat-proof", journal: Buffer.from(JOURNAL, "hex"), proof, accepted: false, present: true },
  ]
  cases[0].journal[0] ^= 1

  const observations = []
  for (const test of cases) {
    const data = RecordedBatchContract.encodeCall("VerifyRecordedBatch", [DOMAIN, BATCH, test.journal, test.proof])
    const tx = AccountBlockTemplate.callContract(SETTLEMENT_ADDRESS, ZNN_ZTS, 0n, data)
    assert.equal(tx.data.length, 420)
    const sent = await sendAndWait(sender, tx)
    assert.equal(sent.accepted, test.accepted, `unexpected ${test.label} receive status`)
    await waitHeight(sent.receiveHeight + CONFIRMATION_DEPTH)
    const included = await zenon.ledger.getAccountBlockByHash(sent.hash)
    const receive = included?.pairedAccountBlock
    assert.ok(included && receive, "missing confirmed send or receive block")
    assert.equal(receive.data.length, 8)
    const receiveCode = Number(receive.data.readBigUInt64BE())
    assert.equal(receiveCode, test.accepted ? 1 : 2)
    const observed = await storage.read(sent.receiveHeight, RECEIPT_KEY)
    assert.equal(observed.present, test.present, `unexpected ${test.label} receipt presence`)
    assert.equal(observed.value ? Buffer.from(observed.value).toString("hex") : null,
      test.present ? expected.toString("hex") : null)
    const [header] = (await zenon.ledger.getMomentumsByHeight(sent.receiveHeight, 1)).list
    assert.ok(header?.stateRoot && header.version === 3 && header.chainIdentifier === CHAIN_ID)
    await checkProducer(sent.receiveHeight, header.hash.toString(), header.stateRoot.toString())
    const raw = await zenon.ledger.getProof(sent.receiveHeight, settlement.stateTreeKey(RECEIPT_KEY))
    assert.equal(raw.root.toString(), observed.root.toString())
    observations.push({
      label: test.label,
      send: sent.hash.toString(),
      receive: receive.hash.toString(),
      receiveCode,
      momentumHeight: sent.receiveHeight,
      momentumHash: header.hash.toString(),
      stateRoot: observed.root.toString(),
      basePlasma: included.basePlasma,
      usedPlasma: included.usedPlasma,
      dataBytes: tx.data.length,
      value: observed.value ? Buffer.from(observed.value).toString("base64") : null,
      stateProof: Buffer.from(raw.proof).toString("base64"),
    })
    console.log(`SETTLEMENT_DEVNET ${test.label} receive=${receiveCode} height=${sent.receiveHeight}`)
  }
  await writeFile(evidencePath, JSON.stringify({
    schema: "zeno-settlement-r4-capture-v1",
    chainIdentifier: CHAIN_ID,
    domain: DOMAIN,
    batch: Number(BATCH),
    journal: JOURNAL,
    guestKey,
    proofSha256: proofDigest.toString("hex"),
    receiptKey: RECEIPT_KEY.toString("hex"),
    observations,
  }) + "\n", { flag: "wx", mode: 0o600 })
}

await main(process.argv[2], process.argv[3], process.argv[4])
