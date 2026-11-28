"""Select a withdrawal checkpoint from two native, loopback devnet nodes."""

import argparse
import base64
from copy import deepcopy
from hashlib import sha256, sha3_256
import json
from pathlib import Path
from struct import pack
import subprocess
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from regtest import RegtestError, require
from regtest_release import (
    b64, digest32, read_json, read_leaf, release, settlement_key, verify_eligible_batch, write_private, zenon_address_bytes,
)


NODE_REVISION = "2835c436b197fece5c76adf4ba8dc1decca2e3ea"
GENESIS = "fab7609bae7fd78eb7d2b0d4a5023832f1a6730921b862f2415da25524698dc9"
NODES = {"producer": 35991, "rpc": 35997}
SUCCESSORS = 30
MAX_REPLAY_LAG = 60
MAX_RPC = 64 * 1024
MAX_RECORD = 256 * 1024
SETTLEMENT_KEYS = {
    "batch": b"\x02" + pack(">IQ", 1, 4),
    "cursor": b"\x03" + pack(">I", 1),
    "anchor": b"\xf1" + pack(">I", 1),
}
OPENER = build_opener(ProxyHandler({}))


class DevnetRPCError(RegtestError):
    """A refusal from the disposable node's native checkpoint method."""


def rpc(port, method, *params):
    request = Request(
        f"http://127.0.0.1:{port}",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("ascii"),
        headers={"Content-Type": "application/json"},
    )
    with OPENER.open(request, timeout=8) as response:
        raw = response.read(MAX_RPC + 1)
    require(0 < len(raw) <= MAX_RPC, "Devnet response exceeds its size limit")
    result = json.loads(raw)
    require(result.get("id") == 1, "Devnet response does not match the request")
    if result.get("error") is not None:
        raise DevnetRPCError(result["error"]["message"])
    require(result.get("result") is not None, "Devnet response has no result")
    return result["result"]


def checkpoint_fields(header):
    return header["height"], header["hash"], header["stateRoot"]


def checkpoint_summary(header):
    return {field: header[field] for field in ("height", "hash", "previousHash", "stateRoot", "producer")}


def validate_view(view, eligible):
    require(view["chainId"] == 69 and view["genesis"] == GENESIS, "Unexpected devnet network")
    height = eligible["height"]
    require(SUCCESSORS <= view["frontierHeight"] - height <= MAX_REPLAY_LAG,
            "Native checkpoint is outside the permitted successor window")
    digest32(view["frontierHash"])
    headers = view["headers"]
    require(len(headers) == SUCCESSORS + 1 and checkpoint_fields(headers[0]) ==
            (height, eligible["hash"], eligible["root"]), "Native segment differs from the eligible checkpoint")
    for offset, header in enumerate(headers):
        require(type(header["height"]) is int and header["height"] == height + offset, "Native segment height differs")
        digest32(header["hash"])
        digest32(header["previousHash"])
        digest32(header["stateRoot"])
        key = b64(header["publicKey"])
        require(len(key) == 32 and zenon_address_bytes(header["producer"]) == b"\x00" + sha3_256(key).digest()[:19],
                "Native producer does not match its public key")
        if offset:
            require(header["previousHash"] == headers[offset - 1]["hash"], "Native segment is not linked")
    require(view["frontierHeight"] != headers[-1]["height"] or view["frontierHash"] == headers[-1]["hash"],
            "Native segment disagrees with the reported frontier")
    return headers


def agree(views, eligible):
    producer = validate_view(views["producer"], eligible)
    replica = validate_view(views["rpc"], eligible)
    require(producer == replica, "Native nodes disagree about the selected branch")
    return producer


def verify_cut_state(proven, header, eligible):
    require(proven["root"] == header["stateRoot"], "Settlement proof does not use the selected branch root")
    for label, key in SETTLEMENT_KEYS.items():
        expected = read_leaf(eligible, label, key)
        require(expected is not None and read_leaf(proven, label, key) == expected,
                "Settlement state changed before the native checkpoint")


def capture_cut_state(port, header, eligible):
    proven = {"root": header["stateRoot"]}
    for label, key in SETTLEMENT_KEYS.items():
        tree_key = settlement_key(key)
        result = rpc(port, "ledger.getProof", header["height"], base64.b64encode(tree_key).decode("ascii"))
        require(result["root"] == proven["root"], "Settlement proof does not use the selected branch root")
        proven[label] = {"key": key.hex(), "treeKey": tree_key.hex(), "value": result["value"], "proof": result["proof"]}
    verify_cut_state(proven, header, eligible)
    return proven


def select_checkpoint(eligible):
    views = {name: rpc(port, "ledger.getVaultCheckpoint", eligible["height"]) for name, port in NODES.items()}
    headers = agree(views, eligible)
    cut = headers[-1]
    return {
        "schema": 2,
        "nodeRevision": NODE_REVISION,
        "chainId": 69,
        "genesis": GENESIS,
        "requiredSuccessors": SUCCESSORS,
        "selected": checkpoint_summary(headers[0]),
        "cut": checkpoint_summary(cut),
        "headers": headers,
        "cutState": {name: capture_cut_state(port, cut, eligible) for name, port in NODES.items()},
        "observedFrontiers": {
            name: {"height": view["frontierHeight"], "hash": view["frontierHash"]}
            for name, view in views.items()
        },
    }


def verify_early_refusal(eligible):
    for port in NODES.values():
        try:
            rpc(port, "ledger.getVaultCheckpoint", eligible["height"])
        except DevnetRPCError as exc:
            require(str(exc) == "vault checkpoint needs 30 canonical successors",
                    "The early view was refused for an unexpected reason")
        else:
            raise RegtestError("A devnet node accepted the early checkpoint")


def wait_for_cut(height):
    deadline = time.monotonic() + 8 * 60
    while time.monotonic() < deadline:
        try:
            tips = [rpc(port, "ledger.getFrontierMomentum") for port in NODES.values()]
            if all(tip["height"] >= height for tip in tips):
                return
        except (URLError, TimeoutError):
            pass
        time.sleep(3)
    raise RegtestError("The two devnet nodes did not reach the required successor")


def verify_conflicting_view_refusal(result, eligible):
    common = {
        "chainId": 69, "genesis": GENESIS, "frontierHeight": result["cut"]["height"],
        "frontierHash": result["cut"]["hash"], "headers": result["headers"],
    }
    disagreeing = deepcopy(common)
    disagreeing["frontierHash"] = sha256(b"synthetic conflicting devnet view").hexdigest()
    disagreeing["headers"][-1]["hash"] = disagreeing["frontierHash"]
    try:
        agree({"producer": common, "rpc": disagreeing}, eligible)
    except RegtestError as exc:
        require(str(exc) == "Native nodes disagree about the selected branch", "Unexpected branch comparison result")
    else:
        raise RegtestError("Conflicting native views were accepted")


def retain_checkpoint(work, result):
    payload = (json.dumps(result, separators=(",", ":")) + "\n").encode("ascii")
    require(len(payload) <= MAX_RECORD, "Native checkpoint record exceeds its size limit")
    write_private(work / "native-checkpoint.json", payload)
    print(f"NATIVE_CHECKPOINT {result['selected']['height']} {result['cut']['height']} {result['cut']['hash']}", flush=True)


def check_proposal_network(work):
    pending = read_json(work / "pending.json", 8192)
    require(pending["chainId"] == 69 and pending["genesis"] == GENESIS,
            "The validity proposal uses a different devnet")
    return pending


def eligible_checkpoint(work):
    pending = check_proposal_network(work)
    commitment = b64(read_json(work / "proposal.json", 49_152)["commitment"])
    _, eligible = verify_eligible_batch(work, commitment, pending)
    return eligible


def attest(work, *, run_controls):
    eligible = eligible_checkpoint(work)
    if run_controls:
        verify_early_refusal(eligible)
    wait_for_cut(eligible["height"] + SUCCESSORS)
    result = select_checkpoint(eligible)
    if run_controls:
        verify_conflicting_view_refusal(result, eligible)
        result["controls"] = ["early-native-view-refused", "conflicting-native-view-refused"]
    retain_checkpoint(work, result)


def replay_checkpoint(work, verifier):
    eligible = eligible_checkpoint(work)
    record = read_json(work / "native-checkpoint.json", MAX_RECORD)
    require((record["schema"], record["nodeRevision"], record["requiredSuccessors"]) == (2, NODE_REVISION, SUCCESSORS),
            "Unexpected native checkpoint record")
    require(set(record["observedFrontiers"]) == set(record["cutState"]) == set(NODES), "Expected both node records")
    headers = record["headers"]
    for name, frontier in record["observedFrontiers"].items():
        validate_view({"chainId": record["chainId"], "genesis": record["genesis"], "headers": headers,
                       "frontierHeight": frontier["height"], "frontierHash": frontier["hash"]}, eligible)
        verify_cut_state(record["cutState"][name], headers[-1], eligible)
    selected, cut = checkpoint_summary(headers[0]), checkpoint_summary(headers[-1])
    require(record["selected"] == selected and record["cut"] == cut, "Native summary differs from its signed headers")

    def check_signatures(segment):
        payload = json.dumps({"headers": segment}, separators=(",", ":")).encode("ascii")
        return subprocess.run([str(verifier)], input=payload, capture_output=True, timeout=10)

    require(check_signatures(headers).returncode == 0, "Offline Momentum signature verification failed")
    changed = [{**headers[0], "signature": base64.b64encode(bytes(64)).decode("ascii")}, *headers[1:]]
    rejected = check_signatures(changed)
    require(rejected.returncode == 1 and rejected.stderr == b"Momentum signature differs\n",
            "The signature control did not refuse the altered witness")

    changed_state = deepcopy(record["cutState"]["rpc"])
    value = b64(changed_state["batch"]["value"])
    changed_state["batch"]["value"] = base64.b64encode(bytes([value[0] ^ 1]) + value[1:]).decode("ascii")
    try:
        verify_cut_state(changed_state, cut, eligible)
    except RegtestError as exc:
        require(str(exc) == "State proof leaf differs", "The state proof control refused for an unexpected reason")
    else:
        raise RegtestError("The state proof control accepted a different batch")

    result = {"schema": 1, "selected": selected, "cut": cut, "headerSignatures": len(headers),
              "cutStatePaths": len(SETTLEMENT_KEYS) * len(NODES),
              "controls": ["altered signature refused", "altered batch leaf refused"]}
    write_private(work / "native-replay.json", (json.dumps(result, separators=(",", ":")) + "\n").encode("ascii"))
    print(f"Verified {result['headerSignatures']} Momentum signatures and {result['cutStatePaths']} cut state paths")


def release_after_checkpoint(work, bitcoind):
    check_proposal_network(work)
    registration = read_json(work / "registration.json", 16_384)

    def guard(eligible):
        wait_for_cut(eligible["height"] + SUCCESSORS)
        result = select_checkpoint(eligible)
        retain_checkpoint(work, result)
        return {"requiredSuccessors": SUCCESSORS, "cut": result["cut"]}

    release(work, bitcoind, registration, checkpoint_guard=guard)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-genesis", action="store_true")
    modes.add_argument("--check-views", action="store_true")
    modes.add_argument("--bitcoind", type=Path)
    modes.add_argument("--replay", type=Path)
    args = parser.parse_args()
    try:
        if args.check_genesis:
            check_proposal_network(args.work)
        elif args.replay is not None:
            replay_checkpoint(args.work, args.replay)
        elif args.bitcoind is not None:
            release_after_checkpoint(args.work, args.bitcoind)
        else:
            attest(args.work, run_controls=args.check_views)
    except (RegtestError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        detail = str(exc) if isinstance(exc, RegtestError) else type(exc).__name__
        parser.exit(1, f"Native checkpoint verification failed: {detail}\n")


if __name__ == "__main__":
    main()
