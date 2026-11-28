"""Verify the archived validity-backed release against its selected devnet checkpoints."""

import argparse
from hashlib import sha256, sha3_256
import json
from pathlib import Path
from struct import error as StructError, pack, unpack
import subprocess
import sys

from regtest import RegtestError, require
from regtest_release import GUEST_KEY, MODULE, b64, check_proof, digest32, read_leaf, word, zenon_address_bytes
from withdrawal_authorization import REGTEST_GENESIS


PROFILE = Path(__file__).with_name("profile.json")
FILE_LIMITS = {
    "pending.json": 8192,
    "proposal.json": 49_152,
    "statement.json": 2048,
    "authorization.json": 16_384,
    "fixture.json": 8192,
    "acceptance.json": 96 * 1024,
    "release.json": 4096,
}
DOMAIN = pack(">I", 1)
BATCH_KEY = b"\x02" + DOMAIN + pack(">Q", 4)
CURSOR_KEY = b"\x03" + DOMAIN
ANCHOR_KEY = b"\xf1" + DOMAIN


def read_evidence(directory, pins):
    files = {}
    require(set(pins) == set(FILE_LIMITS), "The evidence profile has unexpected member names")
    for name, limit in FILE_LIMITS.items():
        path = directory / name
        require(path.is_file() and not path.is_symlink(), f"Expected a regular file for {name}")
        with path.open("rb") as source:
            payload = source.read(limit + 1)
        require(0 < len(payload) <= limit, f"Evidence size differs for {name}")
        require(sha256(payload).hexdigest() == pins[name], f"Evidence digest differs for {name}")
        files[name] = payload
    return files


def match_checkpoint(view, expected):
    require(all(view.get(field) == value for field, value in expected.items()), "Checkpoint differs from the selected run")


def select_release_checkpoint(view, expected, deadline):
    require(view["height"] >= deadline, "Checkpoint precedes the withdrawal deadline")
    match_checkpoint(view, expected)


def check_checkpoint_controls(acceptance, expected, deadline):
    stale = acceptance["accepted"]
    conflicting = {**acceptance["eligible"], "hash": acceptance["before"]["hash"]}
    for view, rejection in ((stale, "Checkpoint precedes the withdrawal deadline"),
                            (conflicting, "Checkpoint differs from the selected run")):
        try:
            select_release_checkpoint(view, expected, deadline)
        except RegtestError as exc:
            require(str(exc) == rejection, "Checkpoint refused for an unexpected reason")
        else:
            raise RegtestError("The checkpoint policy accepted a mismatched view")


def derive_journal(witness, pending, acceptance, commitment):
    require(witness["schema"] == 1 and witness["firstOrdinal"] == 4 and len(witness["inputs"]) == 3,
            "Unexpected proposal witness profile")
    require(len(pending["sends"]) == len(acceptance["admitted"]["ordinals"]) == 3, "Expected three admitted calls")
    contract = digest32(pending["contract"])
    require(digest32(witness["contract"]) == contract and sha3_256(b64(witness["module"])).digest() == MODULE,
            "Proposed execution belongs to a different contract or module")
    start = sha256(b"zeno-devnet-validity-start-v1\x00" + pack(">QIQQ", 69, 1, 4, 4) + commitment[46:78]).digest()
    require(digest32(witness["inputChainStart"]) == start, "The initial input link differs")

    before = acceptance["before"]
    admitted = acceptance["admitted"]
    counter = read_leaf({**before, "count": admitted["count"]}, "count", b"\x1f" + DOMAIN)
    require(counter == (7).to_bytes(32, "big"), "Unexpected count of admitted inputs")
    end = start
    indices = []
    for ordinal, (item, send, proof) in enumerate(zip(witness["inputs"], pending["sends"], admitted["ordinals"]), 4):
        ctx = item["Ctx"]
        index = ctx["GlobalInputIndex"]
        source_hash = digest32(send["hash"])
        leaf = read_leaf({**before, "ordinal": proof}, "ordinal", b"\x20" + DOMAIN + pack(">Q", ordinal))
        require(leaf == index.to_bytes(32, "big") + source_hash, "The admitted ordinal does not bind the source send")
        raw = b64(item["Payload"])
        require(len(raw) >= 141 and raw[:65] == b"\x00" + source_hash + contract and raw[73:137] == bytes(64),
                "Expected a zero-deposit call to the selected authorizer")
        body = raw[141:]
        require(len(body) == int.from_bytes(raw[137:141], "big") and ctx["MomentumHeight"] > 0 and ctx["MomentumTime"] > 0,
                "Invalid admitted call encoding")
        call = (pack(">QQ", ordinal, index) + source_hash + zenon_address_bytes(ctx["L1Sender"]) +
                pack(">QQ", ctx["MomentumHeight"], ctx["MomentumTime"]) + digest32(ctx["MomentumHash"]) +
                contract + raw[65:73] + pack(">Q", len(body)) + body)
        end = sha256(b"zeno-devnet-validity-call-v1\x00" + end + call).digest()
        indices.append(index)
    require(indices[0] < indices[1] < indices[2] and pack(">QQ", indices[0], indices[2]) == commitment[30:46],
            "The admitted input range differs from the batch")
    journal = sha256(b"zeno-devnet-validity-proposal-v1\x00" + pack(">QQI", 69, 4, 3) +
                     start + end + pack(">Q", len(commitment)) + commitment).hexdigest()
    return journal, end


def verify_settlement(acceptance, checkpoints, commitment, end):
    entries = {}
    for name in ("before", "resumed", "accepted", "eligible"):
        view = acceptance[name]
        match_checkpoint(view, checkpoints[name])
        entry = read_leaf(view, "batch", BATCH_KEY)
        cursor = read_leaf(view, "cursor", CURSOR_KEY)
        anchor = read_leaf(view, "anchor", ANCHOR_KEY)
        if name in ("before", "resumed"):
            require(entry is None and anchor is None and (word(cursor, 0), word(cursor, 1)) == (4, 4),
                    "Expected an unaccepted proposal")
        else:
            require(entry is not None and anchor == pack(">QQ", 5, 7) + end and
                    (word(cursor, 0), word(cursor, 1)) == (7, 5), "Unexpected accepted batch or successor cursor")
            entries[name] = entry
    require(acceptance["accepted"]["receiveCode"] == 1 and entries["accepted"] == entries["eligible"],
            "Batch acceptance differs from the eligible entry")
    entry = entries["eligible"]
    require(entry[96:320] == commitment[46:270] and word(entry, 0) == 1 and
            word(entry, 1) == int.from_bytes(commitment[30:38], "big") and
            word(entry, 2) == int.from_bytes(commitment[38:46], "big"), "Batch entry does not bind the commitment")
    submitted, deadline = word(entry, 12), word(entry, 13)
    require(0 < submitted <= acceptance["accepted"]["height"] < deadline and deadline - submitted == 30 and
            word(entry, 16) == 0 and acceptance["eligible"]["finalizeAtHeight"] == deadline, "Withdrawal delay differs")
    select_release_checkpoint(acceptance["eligible"], checkpoints["eligible"], deadline)
    check_checkpoint_controls(acceptance, checkpoints["eligible"], deadline)
    return deadline


def verify_target(receipt, pending, acceptance, commitment, authorization):
    bitcoin = receipt["bitcoin"]
    target = bytes.fromhex(receipt["target"])
    require(len(target) == 100 and target[:32].hex() == bitcoin["genesis"] == REGTEST_GENESIS and
            target[32:64] == digest32(bitcoin["vaultTxid"]), "The receipt does not identify the selected Bitcoin vault")
    vout, amount, size = unpack(">IQH", target[64:78])
    require((vout, amount, size) == (bitcoin["vaultVout"], bitcoin["payoutSats"], 22) and amount == 999_000 and
            target[78:80] == b"\x00\x14", "The receipt does not encode the fixed payout")
    require(receipt["schema"] == 1 and bitcoin["confirmations"] == 1 and 0 < bitcoin["height"] < bitcoin["refundHeight"],
            "Unexpected regtest confirmation or refund window")
    for name in ("txid", "block", "fundingTip", "signedBytesSha256"):
        digest32(bitcoin[name])

    contract = digest32(pending["contract"])
    digest = sha3_256(b"zenon-btc-vault/contract-authorization\x00\x01" + pack(">QI", 69, 1) + contract + target).digest()
    pre, post = commitment[46:78], commitment[78:110]
    require(digest32(receipt["authorizationCommitment"]) == digest32(pending["authorization"]) == digest and
            digest32(pending["moduleHash"]) == MODULE, "The Bitcoin target does not bind the authorization")
    require(digest32(authorization["contract"]) == contract and digest32(authorization["preState"]) == pre and
            digest32(authorization["postState"]) == post, "The authorization proofs use different roots")
    check_proof(sha3_256(contract + b"\x01").digest(), pre, b64(authorization["priorAbsence"]), None)
    fields = ((b"\x00\x02", MODULE), (b"\x00\x03", b"\x00"), (b"\x01", digest))
    require(len(authorization["proofs"]) == len(fields), "Expected three post-state paths")
    for leaf, (key, value) in zip(authorization["proofs"], fields):
        require(b64(leaf["value"]) == value, "The authorization leaf differs")
        check_proof(sha3_256(contract + key).digest(), post, b64(leaf["proof"]), value)

    accepted, eligible = acceptance["accepted"], acceptance["eligible"]
    require(receipt["zeno"] == {
        "chainId": 69, "domain": 1, "batch": 4, "contract": contract.hex(), "acceptedAt": accepted["height"],
        "send": accepted["send"], "postStateRoot": post.hex(), "eligibleAt": eligible["height"],
        "eligibleMomentum": eligible["hash"], "eligibleRoot": eligible["root"],
    }, "The receipt points to a different accepted batch")
    return bitcoin


def audit(directory, verifier, key):
    profile = json.loads(PROFILE.read_bytes())
    files = read_evidence(directory, profile["files"])
    evidence = {name: json.loads(raw) for name, raw in files.items()}
    pending = evidence["pending.json"]
    witness = evidence["proposal.json"]
    acceptance = evidence["acceptance.json"]
    statement = evidence["statement.json"]
    fixture = evidence["fixture.json"]
    for packet in (pending, acceptance):
        require((packet["schema"], packet["chainId"], packet["domain"], packet["batch"]) == (1, 69, 1, 4),
                "Unexpected recorded devnet profile")
    require(acceptance["genesis"] == pending["genesis"] and acceptance["inputSends"] == pending["sends"],
            "Acceptance belongs to a different proposal")
    match_checkpoint(pending, profile["checkpoints"]["before"])

    commitment = b64(witness["commitment"])
    require(len(commitment) == 307 and commitment[:2] == b"\x00\x01" and
            commitment[18:30] == DOMAIN + pack(">Q", 4) and commitment[291:] == bytes(16) and
            commitment == b64(acceptance["commitment"]), "Unexpected canonical batch commitment")
    journal, end = derive_journal(witness, pending, acceptance, commitment)
    proof = bytes.fromhex(fixture["proof"])
    require(journal == statement["journal"] == acceptance["journal"] == fixture["journal"] and
            end.hex() == statement["chainEnd"] == acceptance["chainEnd"] and
            acceptance["guestKey"] == fixture["guestKey"] == GUEST_KEY and
            len(proof) == 256 and sha256(proof).hexdigest() == acceptance["proofSha256"],
            "The derived journal, input anchor or accepted proof digest differs")
    deadline = verify_settlement(acceptance, profile["checkpoints"], commitment, end)
    bitcoin = verify_target(evidence["release.json"], pending, acceptance, commitment, evidence["authorization.json"])
    subprocess.run(
        [str(verifier.resolve()), str(key.resolve()), journal],
        input=files["fixture.json"], stdout=sys.stderr, check=True, timeout=30,
    )
    return {
        "sourceRun": profile["run"], "sourceAttempt": profile["attempt"], "sourceRevision": profile["source"],
        "settlementProofs": 16, "contractProofs": 4, "journal": journal,
        "groth16ProofSha256": acceptance["proofSha256"], "eligibleMomentum": deadline,
        "checkpointControls": ["stale", "conflicting"],
        "recordedBitcoinTxid": bitcoin["txid"], "recordedPayoutSats": bitcoin["payoutSats"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("verifier", type=Path)
    parser.add_argument("key", type=Path)
    args = parser.parse_args()
    try:
        result = audit(args.evidence, args.verifier, args.key)
    except (RegtestError, OSError, ValueError, KeyError, IndexError, TypeError, AttributeError,
            OverflowError, StructError, subprocess.SubprocessError) as exc:
        detail = str(exc) if isinstance(exc, RegtestError) else type(exc).__name__
        parser.exit(1, f"Release audit failed: {detail}\n")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
