"""Decode and verify the fixed Zenon state proof format."""

import base64
from hashlib import sha3_256
import json
import os
from struct import pack

from regtest import require


ZERO = bytes(32)
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
