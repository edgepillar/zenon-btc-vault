"""Add the R4 method to a pinned disposable devnet checkout."""

import hashlib
import re
import shutil
import sys
from pathlib import Path
from urllib.request import urlopen


ZIREN = "c9988a098d92bc55a85ca9307c470d1f97be3337"
VK_URL = f"https://raw.githubusercontent.com/ProjectZKM/Ziren/{ZIREN}/crates/verifier/bn254-vk/groth16_vk.bin"
VK_SHA = "b3e14fe98042c3ff6f219172965bddd8dda586c556adac9b4acbd285e13d3cdb"
ABI_ANCHOR = '\t\t{"type":"function","name":"SubmitBatch","inputs":[\n'
ABI_ENTRY = '''\t\t{"type":"function","name":"VerifyRecordedBatch","inputs":[
\t\t\t{"name":"domainId","type":"uint32"},
\t\t\t{"name":"batchId","type":"uint64"},
\t\t\t{"name":"journal","type":"hash"},
\t\t\t{"name":"proof","type":"bytes"}]},
'''
DISPATCH_ANCHOR = '\t\t\tcabi.SettlementSubmitBatchMethodName:'
DISPATCH_ENTRY = '\t\t\t"VerifyRecordedBatch": &implementation.SettlementVerifyRecordedBatchMethod{MethodName: "VerifyRecordedBatch"},\n'


def insert(path, anchor, prefix):
    original = path.read_text(encoding="utf-8")
    if original.count(anchor) != 1 or prefix in original:
        raise ValueError("Pinned Settlement integration point differs")
    path.write_text(original.replace(anchor, prefix + anchor), encoding="utf-8")


if __name__ == "__main__":
    node = Path(sys.argv[1])
    guest_key = sys.argv[2]
    if re.fullmatch(r"0x[0-9a-f]{64}", guest_key) is None:
        raise ValueError("Expected a pinned Ziren guest key")
    with urlopen(VK_URL, timeout=20) as response:
        circuit_key = response.read(429)
    if len(circuit_key) != 428 or hashlib.sha256(circuit_key).hexdigest() != VK_SHA:
        raise ValueError("Ziren Groth16 verification key differs")
    embedded = node / "vm/embedded"
    implementation = embedded / "implementation"
    insert(embedded / "definition/settlement.go", ABI_ANCHOR, ABI_ENTRY)
    insert(embedded / "embedded.go", DISPATCH_ANCHOR, DISPATCH_ENTRY)
    shutil.copyfile(Path(__file__).with_name("settlement_groth16_devnet.go"), implementation / "settlement_groth16_devnet.go")
    (implementation / "vault-r4-vk.bin").write_bytes(circuit_key)
    (implementation / "vault-r4-guest-key.txt").write_text(guest_key + "\n", encoding="ascii")
