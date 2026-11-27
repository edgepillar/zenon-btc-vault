"""Reconstruct the recorded conformance vector without the Go encoder."""

import base64
import hashlib
import json
import struct
import sys
from pathlib import Path


CHAIN_ID = 69
ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def sender_bytes(address):
    if len(address) != 40 or not address.startswith("z1"):
        raise ValueError("Expected a Zenon address in the pinned vector")
    value = 0
    # The fixed-width vector carries 32 payload words and six checksum words.
    for word in address[2:-6]:
        value = (value << 5) | ALPHABET.index(word)
    return value.to_bytes(20, "big")


def input_record(item, ordinal):
    wire = base64.b64decode(item["Payload"], validate=True)
    if len(wire) < 141 or wire[0] != 0 or any(wire[73:137]):
        raise ValueError("Expected a zero-deposit CallL2 in the pinned vector")
    size = int.from_bytes(wire[137:141], "big")
    payload = wire[141:]
    if size != len(payload):
        raise ValueError("The input payload length differs")
    ctx = item["Ctx"]
    return b"".join((
        struct.pack(">QQ", ordinal, ctx["GlobalInputIndex"]), wire[1:33],
        sender_bytes(ctx["L1Sender"]),
        struct.pack(">QQ", ctx["MomentumHeight"], ctx["MomentumTime"]),
        bytes.fromhex(ctx["MomentumHash"]), wire[33:73], struct.pack(">Q", size), payload,
    ))


def main(proposal, native_log):
    witness = json.loads(Path(proposal).read_bytes())
    commitment = base64.b64decode(witness["commitment"], validate=True)
    domain, batch = struct.unpack_from(">IQ", commitment, 18)
    ordinal = witness["firstOrdinal"]
    if (domain, batch, ordinal, len(witness["inputs"])) != (1, 4, 4, 3):
        raise ValueError("Unexpected conformance vector range")
    pre_state = commitment[46:78]
    start = hashlib.sha256(b"zeno-devnet-validity-start-v1\0" +
                           struct.pack(">QIQQ", CHAIN_ID, domain, batch, ordinal) + pre_state).digest()
    if start.hex() != witness["inputChainStart"]:
        raise ValueError("The independently encoded input chain seed differs")
    end = start
    for i, item in enumerate(witness["inputs"]):
        end = hashlib.sha256(b"zeno-devnet-validity-call-v1\0" + end + input_record(item, ordinal + i)).digest()
    public = (b"zeno-devnet-validity-proposal-v1\0" + struct.pack(">QQI", CHAIN_ID, ordinal, 3) +
              start + end + struct.pack(">Q", len(commitment)) + commitment)
    journal = hashlib.sha256(public).hexdigest()
    lines = Path(native_log).read_text(encoding="ascii").splitlines()
    if lines.count(f"PROPOSAL_JOURNAL {journal}") != 1 or lines.count(f"PROPOSAL_CHAIN_END {end.hex()}") != 1:
        raise ValueError("The independent statement differs from the native replay")
    print(f"PROPOSAL_VECTOR_VERIFIED {journal} {end.hex()}")


if __name__ == "__main__":
    main(*sys.argv[1:])
