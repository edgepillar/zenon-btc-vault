import assert from "node:assert/strict"
import { createHash } from "node:crypto"
import { readFile, stat, writeFile } from "node:fs/promises"
import {
  l1Caller, parseInterface, prepareCall, prepareDeploy, sha3, verifyProofs, wasmPathDerivation,
  type ContractView, type ReadResult, type StorageView,
} from "zeno-client"
import { Hash, settlement, type KeyPair } from "znn-typescript-sdk"
import { CHAIN_ID, CONFIRMATION_DEPTH, DOMAIN_ID, keyPair, waitFor } from "../src/devnet.js"
import { observeBatch, proveBatch, type Checkpoint, type Snapshot } from "./batch-proof.js"
import {
  PLASMA_LIMIT, buyStorage, docker, executorDown, expectStatus, flow, send, track, user, type Ctx, type Vault,
} from "./lib.js"

const MAX_EVIDENCE_BYTES = 65_536
const GENESIS = "0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206"
const COMMITMENT_KEY = "01"
const IMMUTABLE_METADATA = Buffer.from("0001" + "00".repeat(21), "hex")

interface Selection {
  contractId: string
  sourceCodeHash: string
  bitcoinTarget: string
  batchId: number
  checkpoint: Checkpoint
  earlyCheckpoint: Checkpoint
}

interface Evidence {
  schemaVersion: 1
  selection: Selection
  final: Snapshot
  early: Snapshot
  contract: ReadResult<ContractView>
  storage: ReadResult<StorageView>
  sends: { unauthorized: string; authorize: string; overwrite: string }
}

function targetCommitment(contractId: string, target: string): string {
  assert.match(contractId, /^[0-9a-f]{64}$/, "expected a 32-byte contract ID")
  assert.match(target, /^(?:[0-9a-f]{2}){79,200}$/, "invalid Bitcoin target encoding")
  assert.ok(target.startsWith(GENESIS), "the test requires Bitcoin regtest")
  const bytes = Buffer.from(target, "hex")
  assert.equal(bytes.readUInt16BE(76), bytes.length - 78, "Bitcoin target script length differs")
  const source = Buffer.alloc(12)
  source.writeBigUInt64BE(BigInt(CHAIN_ID))
  source.writeUInt32BE(DOMAIN_ID, 8)
  return Buffer.from(sha3(
    Buffer.from("zenon-btc-vault/contract-authorization\x00\x01"),
    source, Buffer.from(contractId, "hex"), bytes,
  )).toString("hex")
}

function proveView(read: ReadResult, root: string, batchId: number, view: string, params: Record<string, string>) {
  assert.equal(read.domainId, DOMAIN_ID, "unexpected view domain")
  assert.equal(read.batchId, batchId, "unexpected view batch")
  assert.equal(read.postStateRoot, root, "view root differs from the authenticated batch")
  assert.equal(read.view, view, "unexpected view type")
  const leaves = verifyProofs(view, root, read.proofs)
  assert.ok(wasmPathDerivation.verifyView(view, params, read, leaves), "view lacks path verification")
}

async function verify(evidence: Evidence, selected: Selection, snapshot: Snapshot, checkpoint: Checkpoint) {
  assert.equal(evidence.schemaVersion, 1, "unexpected evidence schema")
  assert.ok(Number.isSafeInteger(selected.batchId) && selected.batchId > 0, "invalid batch number")
  const batch = await proveBatch(snapshot, checkpoint, DOMAIN_ID, selected.batchId)
  const abiRoot = String(batch.postStateRoot)
  assert.match(abiRoot, /^0x[0-9a-fA-F]{64}$/, "expected a 32-byte ABI state root")
  const root = abiRoot.slice(2).toLowerCase()
  const { contract, storage } = evidence
  const contractId = selected.contractId
  proveView(contract, root, selected.batchId, "contract", { contractId })
  assert.equal(contract.result.sourceCodeHash, selected.sourceCodeHash, "unexpected as-deployed module")
  assert.equal(contract.result.upgradePolicy, "IMMUTABLE", "expected an immutable contract")
  proveView(storage, root, selected.batchId, "storage", { contractId, key: COMMITMENT_KEY })
  assert.equal(storage.result.value, targetCommitment(contractId, selected.bitcoinTarget), "contract did not authorize this Bitcoin target")
  return Number(batch.finalizeAtHeight)
}

async function checkEvidence(evidence: Evidence, selected: Selection) {
  const { early, final, storage } = evidence
  const height = await verify(evidence, selected, final, selected.checkpoint)
  assert.equal(selected.earlyCheckpoint.height, height - 1, "expected the last ineligible height")
  await assert.rejects(verify(evidence, selected, early, selected.earlyCheckpoint), /before the batch finalizes/)
  await assert.rejects(verify(evidence, selected, final, selected.earlyCheckpoint), /selected devnet checkpoint/)
  const replay = (input: Evidence, trust = selected) => verify(input, trust, final, selected.checkpoint)
  await assert.rejects(replay(evidence, { ...selected, contractId: "01".repeat(32) }), /derived path/)
  await assert.rejects(replay(evidence, { ...selected, sourceCodeHash: "01".repeat(32) }), /unexpected as-deployed module/)
  const changed = selected.bitcoinTarget.slice(0, -2) + (!selected.bitcoinTarget.endsWith("00") ? "00" : "01")
  await assert.rejects(replay(evidence, { ...selected, bitcoinTarget: changed }), /did not authorize this Bitcoin target/)
  await assert.rejects(replay({ ...evidence, storage: { ...storage, batchId: storage.batchId + 1 } }), /unexpected view batch/)
  const changedProof = { ...storage.proofs[0], value: "01".repeat(32) }
  await assert.rejects(replay({ ...evidence, storage: { ...storage, proofs: [changedProof] } }), /inclusion proof/)
  console.log("PASS: a pinned v3 checkpoint proved the immutable contract and its Bitcoin target commitment")
  console.log("PASS: refused an early batch, another checkpoint, contract, module, target, batch and modified leaf")
}

async function deployAuthorizer(ctx: Ctx, artifact: string, separateBatches = false): Promise<Vault> {
  const module = await readFile(`${artifact}.wasm`)
  assert.ok(module.length > 0 && module.length <= 65_536, "unexpected WASM module size")
  const iface = parseInterface(JSON.parse(await readFile(`${artifact}.interface.json`, "utf8")))
  prepareDeploy(module, iface, { interfaceEncodings: ctx.contracts.interfaceEncodings })
  const codeHash = Buffer.from(sha3(module)).toString("hex")
  const api = ctx.zenon.embedded.settlement
  const prices = await api.getPeripheryConfig()
  assert.ok(prices, "Settlement bytecode prices are unavailable")
  const plan = settlement.planDeploy(module, IMMUTABLE_METADATA, { znnPerByteOfBytecode: prices.znnPerByteOfBytecode }, 0)
  const start = api.deployContractStart(DOMAIN_ID, plan.codeHash, plan.metadataHash, plan.totalSize, plan.chunkCount, IMMUTABLE_METADATA)
  const started = track(ctx, "authorizer deploy start", (await send(user, start)).hash, (r) => expectStatus(r, "SUCCESS"))
  if (separateBatches) assert.equal((await started.at("latest")).batchId, 0)
  for (const chunk of plan.chunks) {
    const transaction = api.deployContractChunk(DOMAIN_ID, plan.codeHash, chunk.index, chunk.chunkHash, chunk.data)
    const submitted = track(ctx, `authorizer deploy chunk ${chunk.index}`, (await send(user, transaction)).hash, (r) => expectStatus(r, "SUCCESS"))
    if (separateBatches) assert.equal((await submitted.at("latest")).batchId, 1)
  }
  const { hash } = await send(user, api.deployContractFinalize(DOMAIN_ID, plan.codeHash, plan.fee))
  const deploy = track(ctx, "authorizer deploy", hash, (r) => expectStatus(r, "SUCCESS"))
  const contractId = Hash.parse((await deploy.at("pending")).receipt.contractId)
  assert.notEqual(contractId.toString(), "00".repeat(32), "deployment must have a contract ID")
  if (separateBatches) assert.equal((await deploy.at("latest")).batchId, 2)
  return { iface, codeHash, contractId, deployFee: plan.fee }
}

function sendAuthorization(ctx: Ctx, contract: Vault, caller: KeyPair, digest: string) {
  const transaction = ctx.zenon.embedded.settlement.callL2(
    DOMAIN_ID, contract.contractId, PLASMA_LIMIT,
    prepareCall(contract.iface, "authorize", [Buffer.from(digest, "hex")]),
  )
  return send(caller, transaction)
}

async function authorize(ctx: Ctx, contract: Vault, caller: KeyPair, digest: string, label: string, abortCode: number, queued: boolean) {
  const { hash, receiveHeight } = await sendAuthorization(ctx, contract, caller, digest)
  const receipt = track(ctx, label, hash, (r) => {
    expectStatus(r, abortCode === 0 ? "SUCCESS" : "REVERT")
    assert.equal(r.receipt.contractId, contract.contractId.toString())
    assert.equal(r.receipt.abortCode, abortCode)
    assert.equal(r.outbox.length, 0, "authorization must not withdraw a native asset")
  })
  if (!queued) await receipt.at("pending")
  return { hash: hash.toString(), receipt, receiveHeight }
}

async function capturePending(ctx: Ctx, artifact: string, output: string, targetFile?: string) {
  const deployed = await deployAuthorizer(ctx, artifact, true)
  const quota = await buyStorage(ctx, deployed, 96n)
  assert.equal((await quota.at("latest")).batchId, 3)
  await docker("stop", ctx.executor)
  assert.equal(await docker("inspect", "-f", "{{.State.Running}}", ctx.executor), "false")

  const contract = deployed.contractId.toString()
  const target = targetFile === undefined ? null : (await readFile(targetFile, "utf8")).trim()
  const digest = target === null ?
    createHash("sha256").update("zeno-validity-devnet-authorizer-v1\x00").update(contract, "hex").digest("hex") :
    targetCommitment(contract, target)
  const first = await sendAuthorization(ctx, deployed, keyPair("admin"), digest)
  const second = await sendAuthorization(ctx, deployed, user, digest)
  const third = await sendAuthorization(ctx, deployed, user, "ff".repeat(32))
  const minimum = third.receiveHeight + CONFIRMATION_DEPTH
  const header = await waitFor("the three admitted calls to reach a confirmed checkpoint", 120_000, async () => {
    const current = await ctx.zenon.ledger.getFrontierMomentum()
    return current.height >= minimum && current.version === 3 && current.stateRoot ? current : undefined
  })
  assert.equal(header.chainIdentifier, CHAIN_ID)
  const genesis = (await ctx.zenon.ledger.getMomentumsByHeight(1, 1)).list[0]
  assert.ok(genesis?.hash && genesis.chainIdentifier === CHAIN_ID)
  assert.equal(await ctx.zenon.embedded.settlement.getBatch(DOMAIN_ID, 4), null, "proposed batch is already accepted")
  const pending = {
    schema: 1, chainId: CHAIN_ID, domain: DOMAIN_ID, batch: 4, contract,
    moduleHash: deployed.codeHash, submitter: keyPair("executor").address.toString(),
    genesis: genesis.hash.toString(), height: header.height, hash: header.hash.toString(), root: header.stateRoot!.toString(),
    sends: [first, second, third].map(({ hash, receiveHeight }) => ({ hash: hash.toString(), receiveHeight })),
    ...(target === null ? {} : { authorization: digest }),
  }
  await writeFile(output, JSON.stringify(pending) + "\n", { flag: "wx", mode: 0o600 })
  console.log(`VALIDITY_PENDING ${pending.height} ${pending.hash} ${pending.root}`)
}

async function capture(ctx: Ctx, artifact: string, targetFile: string, output: string, batchMode: boolean) {
  const bitcoinTarget = (await readFile(targetFile, "utf8")).trim()
  const deployed = await deployAuthorizer(ctx, artifact)
  const contractId = deployed.contractId.toString()
  const commitment = targetCommitment(contractId, bitcoinTarget)
  const quota = await buyStorage(ctx, deployed, 96n)
  await quota.at("pending")

  const sendCalls = async () => {
    const unauthorized = await authorize(ctx, deployed, keyPair("admin"), commitment, "non-authorizer call", 1, batchMode)
    const authorized = await authorize(ctx, deployed, user, commitment, "authorize registered Bitcoin target", 0, batchMode)
    const overwrite = await authorize(ctx, deployed, user, "ff".repeat(32), "overwrite authorization", 2, batchMode)
    return [unauthorized, authorized, overwrite] as const
  }
  if (batchMode) await quota.at("latest")
  const calls = batchMode
    ? await executorDown(ctx.executor, "stop", async () => {
      const queued = await sendCalls()
      const required = Math.max(...queued.map((call) => call.receiveHeight)) + CONFIRMATION_DEPTH
      await waitFor("all three queued inputs to be eligible", 120_000, async () => {
        const frontier = (await ctx.zenon.ledger.getFrontierMomentum()).height
        return frontier >= required ? frontier : undefined
      })
      return queued
    })
    : await sendCalls()
  const [unauthorized, authorized, overwrite] = calls
  await Promise.all(calls.map((call) => call.receipt.at("latest")))
  const finalized = await Promise.all(calls.map((call) => call.receipt.at("finalized")))
  const last = finalized[2]
  const batchId = last.batchId
  if (batchMode) {
    assert.ok(finalized.every((r, i) => r.batchId === batchId && r.globalInputIndex === finalized[0].globalInputIndex + i),
      "expected the three calls in order in the same batch")
    const batch = await ctx.gateway.getBatch(DOMAIN_ID, batchId)
    assert.equal(batch.inputs.length, 3, "expected the full batch to hold exactly the three calls")
    console.log(`PASS: all three ordered calls settled in batch ${batchId}`)
  }
  const api = ctx.zenon.embedded.settlement
  const [entry, contract, storage] = await Promise.all([
    api.getBatch(DOMAIN_ID, batchId),
    ctx.contracts.contract(contractId, batchId),
    ctx.contracts.storage(contractId, COMMITMENT_KEY, batchId),
  ])
  assert.ok(entry?.isFinalized, "the authorization batch is ineligible in Settlement")
  const { ledger } = ctx.zenon
  const height = (await ledger.getFrontierMomentum()).height
  const final = await observeBatch(ledger, DOMAIN_ID, batchId, height)
  const early = await observeBatch(ledger, DOMAIN_ID, batchId, entry.finalizeAtHeight - 1)
  const selection: Selection = {
    contractId, sourceCodeHash: deployed.codeHash, bitcoinTarget, batchId,
    checkpoint: final.checkpoint, earlyCheckpoint: early.checkpoint,
  }
  const evidence: Evidence = {
    schemaVersion: 1, selection, final, early, contract, storage,
    sends: { unauthorized: unauthorized.hash, authorize: authorized.hash, overwrite: overwrite.hash },
  }
  const raw = JSON.stringify(evidence) + "\n"
  assert.ok(Buffer.byteLength(raw) <= MAX_EVIDENCE_BYTES, "authorization evidence exceeds its byte limit")
  await writeFile(output, raw, { flag: "wx", mode: 0o600 })
  await checkEvidence(evidence, selection)
  console.log(`PASS: non-authorizer and overwrite calls reverted, authorized commitment ${commitment}`)
}

assert.equal(CHAIN_ID, 69, "expected the isolated Zeno chain")
assert.equal(DOMAIN_ID, 1, "expected the isolated WASM domain")
const [mode, first, second, third] = process.argv.slice(2)
if (mode === "caller") {
  await writeFile(first, l1Caller(user.address).getBytes(), { flag: "wx" })
} else if (mode === "replay") {
  assert.match(second, /^[0-9a-f]{64}$/, "select the expected evidence digest explicitly")
  assert.ok((await stat(first)).size <= MAX_EVIDENCE_BYTES, "authorization evidence exceeds its byte limit")
  const raw = await readFile(first)
  assert.ok(raw.length <= MAX_EVIDENCE_BYTES, "authorization evidence exceeds its byte limit")
  assert.equal(createHash("sha256").update(raw).digest("hex"), second, "evidence differs from the selected capture")
  const evidence: Evidence = JSON.parse(raw.toString("utf8"))
  await checkEvidence(evidence, evidence.selection)
} else if (mode === "capture-pending") {
  flow("pending-validity-proposal", (ctx) => capturePending(ctx, first, second, third))
} else {
  assert.ok(mode === "capture" || mode === "capture-batch", "use caller, capture, capture-batch, or replay")
  flow("contract-authorized-bitcoin-target", (ctx) => capture(ctx, first, second, third, mode === "capture-batch"))
}
