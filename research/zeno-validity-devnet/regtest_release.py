"""Verify a validity-backed authorization before a single Bitcoin regtest spend."""

import argparse
import base64
from dataclasses import asdict
from hashlib import sha256, sha3_256
import json
import os
from pathlib import Path
from struct import pack

from mock_release import RegisteredRelease, Withdrawal
from regtest import COIN, RegtestError, confirm_spend, register_release, require, start_regtest
from regtest_capture import prepare_registration
from withdrawal_authorization import REGTEST_GENESIS, encode_bitcoin_target


ZERO = bytes(32)
MODULE = bytes.fromhex("b372ca2a5ba902364c07af2e2a2719acf4a524fcaed11b130588a77fa9ac73bd")
GUEST_KEY = "0x00e4e8f52c5c085d833abcfacbbf0270e9832cd2af090ae12f7659c08270ffa4"
SETTLEMENT_ADDRESS = "z1qxemdeddedxsettlementxxxxxxxxxxqtmqrx9"
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
MAX_EVIDENCE = 96 * 1024


def write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as out:
        out.write(content)


def read_json(path, maximum=MAX_EVIDENCE):
    with path.open("rb") as source:
        content = source.read(maximum + 1)
    require(0 < len(content) <= maximum, "Evidence file size differs")
    return json.loads(content)


def b64(encoded):
    return base64.b64decode(encoded, validate=True)


def digest32(encoded):
    value = bytes.fromhex(encoded)
    require(len(value) == 32 and value.hex() == encoded, "Expected a canonical 32-byte hexadecimal value")
    return value


def zenon_address_bytes(text):
    require(len(text) == 40 and text.startswith("z1"), "Expected a Zenon address")
    digits = [BECH32.index(character) for character in text[2:]]
    checksum = 1
    for digit in (3, 0, 26, *digits):
        top = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ digit
        for bit, generator in enumerate((0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)):
            if top & (1 << bit):
                checksum ^= generator
    require(checksum == 1, "Zenon address checksum differs")
    address = 0
    for digit in digits[:-6]:
        address = (address << 5) | digit
    return address.to_bytes(20, "big")


def settlement_key(key):
    return b"\x03" + zenon_address_bytes(SETTLEMENT_ADDRESS) + b"\x04" + key


def check_proof(path, root, proof, value):
    require(99 <= len(proof) <= 16_384 and proof[0] == (value is not None), "Unexpected state proof layout")
    require(proof[1:33] == path, "State proof path differs")
    current = ZERO if value is None else sha3_256(path + value).digest()
    require(proof[33:65] == current, "State proof leaf differs")
    bitmap = int.from_bytes(proof[65:97], "big")
    count = int.from_bytes(proof[97:99], "big")
    require(bin(bitmap).count("1") == count, "State proof bitmap differs")
    offset = 99
    bits = int.from_bytes(path, "big")
    for level in range(256):
        sibling = ZERO
        if (bitmap >> (255 - level)) & 1:
            sibling = proof[offset:offset + 32]
            require(len(sibling) == 32 and sibling != ZERO, "Invalid state proof sibling")
            offset += 32
        if current != ZERO or sibling != ZERO:
            pair = sibling + current if (bits >> level) & 1 else current + sibling
            current = sha3_256(pair).digest()
    tail = b"" if value is None else pack(">I", len(value)) + value
    require(proof[offset:] == tail and current == root, "State proof did not reproduce the selected root")


def read_leaf(snapshot, label, key):
    leaf = snapshot[label]
    full_key = settlement_key(key)
    require(leaf["key"] == key.hex() and leaf["treeKey"] == full_key.hex(), "Settlement storage key differs")
    value = None if leaf["value"] is None else b64(leaf["value"])
    check_proof(sha3_256(full_key).digest(), digest32(snapshot["root"]), b64(leaf["proof"]), value)
    return value


def word(raw, index):
    require(len(raw) >= (index + 1) * 32, "Truncated Settlement value")
    return int.from_bytes(raw[index * 32:(index + 1) * 32], "big")


def prepare(work, bitcoind):
    datadir = work / "bitcoin"
    datadir.mkdir(mode=0o700)
    with start_regtest(bitcoind, datadir) as rpc:
        registered, miner, refund_height, vault, tip, genesis = prepare_registration(rpc)
        target = encode_bitcoin_target(registered.withdrawal, genesis)
        registration = {
            "withdrawal": asdict(registered.withdrawal),
            "signed": registered.signed_transaction,
            "vaultScript": rpc.call("validateaddress", vault)["scriptPubKey"],
            "miner": miner,
            "refundHeight": refund_height,
            "tip": tip,
            "height": rpc.call("getblockcount"),
            "genesis": genesis,
        }
    write_private(work / "registration.json", json.dumps(registration, separators=(",", ":")).encode("ascii"))
    write_private(work / "target.hex", target.hex().encode("ascii") + b"\n")
    print(f"RELEASE_REGTEST_REGISTERED {tip} {sha256(bytes.fromhex(registered.signed_transaction)).hexdigest()}")


def verify_contract(work, registration):
    withdrawal = Withdrawal(**registration["withdrawal"])
    target = encode_bitcoin_target(withdrawal, REGTEST_GENESIS)
    with (work / "target.hex").open("rb") as source:
        require(source.read(402) == target.hex().encode("ascii") + b"\n", "Selected Bitcoin target differs")
    pending = read_json(work / "pending.json", 8192)
    require((pending["schema"], pending["chainId"], pending["domain"], pending["batch"]) == (1, 69, 1, 4),
            "Pending proposal uses an unexpected profile")
    contract = digest32(pending["contract"])
    target_digest = sha3_256(b"zenon-btc-vault/contract-authorization\x00\x01" + pack(">QI", 69, 1) + contract + target).digest()
    require(digest32(pending["authorization"]) == target_digest and digest32(pending["moduleHash"]) == MODULE,
            "Captured target or module differs from registration")
    proposal = b64(read_json(work / "proposal.json", 49_152)["commitment"])
    require(len(proposal) == 307 and proposal[18:30] == pack(">IQ", 1, 4), "Unexpected proposal commitment")
    evidence = read_json(work / "authorization.json", 16_384)
    pre_state, post_state = proposal[46:78], proposal[78:110]
    require(digest32(evidence["contract"]) == contract and digest32(evidence["preState"]) == pre_state and
            digest32(evidence["postState"]) == post_state, "Authorization paths do not bind the proposal roots")
    check_proof(sha3_256(contract + b"\x01").digest(), pre_state, b64(evidence["priorAbsence"]), None)
    require(len(evidence["proofs"]) == 3, "Expected three authorization state proofs")
    for leaf, (key, value) in zip(evidence["proofs"], ((b"\x00\x02", MODULE), (b"\x00\x03", b"\x00"), (b"\x01", target_digest))):
        require(b64(leaf["value"]) == value, "Authorization state leaf differs")
        check_proof(sha3_256(contract + key).digest(), post_state, b64(leaf["proof"]), value)
    return withdrawal, target, target_digest, proposal, pending


def verify_eligible_batch(work, proposal, pending):
    acceptance = read_json(work / "acceptance.json")
    statement = read_json(work / "statement.json", 2048)
    fixture = read_json(work / "fixture.json", 8192)
    require(acceptance["schema"] == 1 and (acceptance["chainId"], acceptance["domain"], acceptance["batch"]) == (69, 1, 4),
            "Acceptance uses an unexpected profile")
    require(acceptance["genesis"] == pending["genesis"] and acceptance["inputSends"] == pending["sends"] and
            b64(acceptance["commitment"]) == proposal and fixture["guestKey"] == acceptance["guestKey"] == GUEST_KEY and
            fixture["journal"] == acceptance["journal"] == statement["journal"], "Acceptance differs from the verified pending proposal")
    proof = bytes.fromhex(fixture["proof"])
    require(len(proof) == 256 and sha256(proof).hexdigest() == acceptance["proofSha256"], "The accepted proof digest differs")
    batch_key = bytes([2]) + pack(">IQ", 1, 4)
    cursor_key = bytes([3]) + pack(">I", 1)
    anchor_key = bytes([241]) + pack(">I", 1)
    before, accepted, eligible = (acceptance[name] for name in ("before", "accepted", "eligible"))
    require((before["height"], before["hash"], before["root"]) == (pending["height"], pending["hash"], pending["root"]),
            "The independently selected devnet checkpoint differs")
    require(read_leaf(before, "batch", batch_key) is None, "Proposal was already accepted when proving started")
    require(accepted["receiveCode"] == 1 and before["height"] < accepted["height"] < eligible["height"],
            "Expected a successful receive followed by eligibility")
    entry = read_leaf(eligible, "batch", batch_key)
    require(entry is not None and entry == read_leaf(accepted, "batch", batch_key), "Accepted batch changed before eligibility")
    require(entry[96:320] == proposal[46:270] and word(entry, 0) == int.from_bytes(proposal[:2], "big") and
            word(entry, 1) == int.from_bytes(proposal[30:38], "big") and word(entry, 2) == int.from_bytes(proposal[38:46], "big"),
            "Settlement entry does not commit to the proposed execution")
    submitted, finalizes = word(entry, 12), word(entry, 13)
    require(0 < submitted <= accepted["height"] < finalizes == eligible["finalizeAtHeight"] == eligible["height"] and
            finalizes - submitted == 30 and word(entry, 16) == 0, "The selected devnet entry is ineligible")
    cursor = read_leaf(eligible, "cursor", cursor_key)
    require(cursor is not None and word(cursor, 0) == 7 and word(cursor, 1) == 5, "The validity cursor did not advance")
    anchor = read_leaf(eligible, "anchor", anchor_key)
    require(anchor == pack(">QQ", 5, 7) + digest32(statement["chainEnd"]), "The successor input link differs")
    return accepted, eligible


def release(work, bitcoind, registration, *, checkpoint_guard=None):
    withdrawal, target, commitment, proposal, pending = verify_contract(work, registration)
    accepted, eligible = verify_eligible_batch(work, proposal, pending)
    source = {"txid": withdrawal.txid, "vout": withdrawal.vout}
    with start_regtest(bitcoind, work / "bitcoin", resume_tip=registration["tip"]) as rpc:
        require(rpc.call("getblockhash", 0) == registration["genesis"] == REGTEST_GENESIS, "The Bitcoin genesis differs")
        checkpoint = None if checkpoint_guard is None else checkpoint_guard(eligible)
        tip = rpc.call("getblockchaininfo")
        require(tip["bestblockhash"] == registration["tip"] and
                tip["blocks"] == registration["height"] < registration["refundHeight"], "The Bitcoin release window changed")
        require(not rpc.call("getrawmempool"), "The Bitcoin mempool changed before release")
        signed = registration["signed"]
        checked = register_release(rpc, withdrawal, signed, vault_script=registration["vaultScript"])
        require(isinstance(checked, RegisteredRelease), "The exact signed release was not registered")
        raw = checked.release_signed_transaction(withdrawal)
        require(raw == signed, "The release bytes differ from registration")
        txid, block = confirm_spend(rpc, raw, source, withdrawal.recipient_script_pubkey, registration["miner"])
        confirmed = rpc.call("getrawtransaction", txid, False, block)
        payout = rpc.call("gettxout", txid, 0, False)
        require(bytes.fromhex(confirmed) == bytes.fromhex(signed) and payout["value"] * COIN == withdrawal.amount_sats,
                "Bitcoin Core confirmed a different release payload")
        result = {
            "schema": 1,
            "target": target.hex(),
            "authorizationCommitment": commitment.hex(),
            "zeno": {"chainId": 69, "domain": 1, "batch": 4, "contract": pending["contract"],
                     "acceptedAt": accepted["height"], "send": accepted["send"], "postStateRoot": proposal[78:110].hex(),
                     "eligibleAt": eligible["height"], "eligibleMomentum": eligible["hash"], "eligibleRoot": eligible["root"]},
            "bitcoin": {"genesis": REGTEST_GENESIS, "vaultTxid": withdrawal.txid, "vaultVout": withdrawal.vout,
                        "fundingTip": registration["tip"], "refundHeight": registration["refundHeight"],
                        "txid": txid, "block": block, "height": rpc.call("getblockcount"),
                        "payoutSats": withdrawal.amount_sats, "confirmations": payout["confirmations"],
                        "signedBytesSha256": sha256(bytes.fromhex(confirmed)).hexdigest()},
        }
        if checkpoint is not None:
            result["nativeCheckpoint"] = checkpoint
    write_private(work / "release.json", (json.dumps(result, separators=(",", ":")) + "\n").encode("ascii"))
    print(f"VALIDITY_REGTEST_RELEASE {txid} {block} {withdrawal.amount_sats}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "check-pending", "release"))
    parser.add_argument("work", type=Path)
    parser.add_argument("bitcoind", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "prepare":
            prepare(args.work, args.bitcoind)
        else:
            registration = read_json(args.work / "registration.json", 16_384)
            if args.mode == "check-pending":
                verify_contract(args.work, registration)
                print("RELEASE_TARGET_CHECKED_AGAINST_PROPOSED_STATE")
            else:
                release(args.work, args.bitcoind, registration)
    except (RegtestError, OSError, ValueError, KeyError, TypeError) as exc:
        details = str(exc) if isinstance(exc, RegtestError) else type(exc).__name__
        parser.exit(1, f"Validity-backed regtest qualification failed: {details}\n")


if __name__ == "__main__":
    main()
