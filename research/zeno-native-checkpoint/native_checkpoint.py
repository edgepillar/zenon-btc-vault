"""Select a withdrawal checkpoint from two native, loopback devnet nodes."""

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from struct import pack
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from regtest import RegtestError, require
from regtest_release import b64, digest32, read_json, read_leaf, settlement_key, verify_eligible_batch, write_private


NODE_REVISION = "2835c436b197fece5c76adf4ba8dc1decca2e3ea"
GENESIS = "fab7609bae7fd78eb7d2b0d4a5023832f1a6730921b862f2415da25524698dc9"
NODES = {"producer": 35991, "rpc": 35997}
SUCCESSORS = 30
MAX_REPLAY_LAG = 60
MAX_RPC = 64 * 1024
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
        require(isinstance(header["producer"], str) and header["producer"].startswith("z1"), "Native producer is missing")
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


def check_cut_state(port, header, eligible):
    proven = {"root": header["stateRoot"]}
    keys = {
        "batch": b"\x02" + pack(">IQ", 1, 4),
        "cursor": b"\x03" + pack(">I", 1),
        "anchor": b"\xf1" + pack(">I", 1),
    }
    for label, key in keys.items():
        tree_key = settlement_key(key)
        result = rpc(port, "ledger.getProof", header["height"], base64.b64encode(tree_key).decode("ascii"))
        require(result["root"] == proven["root"], "Settlement proof does not use the selected branch root")
        proven[label] = {"key": key.hex(), "treeKey": tree_key.hex(), "value": result["value"], "proof": result["proof"]}
        require(read_leaf(proven, label, key) == b64(eligible[label]["value"]),
                "Settlement state changed before the native checkpoint")


def select_checkpoint(eligible):
    views = {name: rpc(port, "ledger.getVaultCheckpoint", eligible["height"]) for name, port in NODES.items()}
    headers = agree(views, eligible)
    for port in NODES.values():
        check_cut_state(port, headers[-1], eligible)
    return {
        "schema": 1,
        "nodeRevision": NODE_REVISION,
        "chainId": 69,
        "genesis": GENESIS,
        "requiredSuccessors": SUCCESSORS,
        "selected": headers[0],
        "cut": headers[-1],
        "headers": headers,
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


def attest(work, *, run_controls):
    pending = read_json(work / "pending.json", 8192)
    commitment = b64(read_json(work / "proposal.json", 49_152)["commitment"])
    require(pending["genesis"] == GENESIS, "The validity proposal uses a different genesis")
    _, eligible = verify_eligible_batch(work, commitment, pending)
    if run_controls:
        verify_early_refusal(eligible)
    wait_for_cut(eligible["height"] + SUCCESSORS)
    result = select_checkpoint(eligible)
    if run_controls:
        verify_conflicting_view_refusal(result, eligible)
        result["controls"] = ["early-native-view-refused", "conflicting-native-view-refused"]
    write_private(work / "native-checkpoint.json", (json.dumps(result, separators=(",", ":")) + "\n").encode("ascii"))
    print(f"NATIVE_CHECKPOINT {result['selected']['height']} {result['cut']['height']} {result['cut']['hash']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", type=Path)
    parser.add_argument("--check-views", action="store_true")
    args = parser.parse_args()
    try:
        attest(args.work, run_controls=args.check_views)
    except (RegtestError, OSError, ValueError, KeyError, TypeError) as exc:
        detail = str(exc) if isinstance(exc, RegtestError) else type(exc).__name__
        parser.exit(1, f"Native checkpoint verification failed: {detail}\n")


if __name__ == "__main__":
    main()
