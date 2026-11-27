"""Add the R4 method to a pinned disposable devnet checkout."""

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen


ZIREN = "c9988a098d92bc55a85ca9307c470d1f97be3337"
ZENON = "2835c436b197fece5c76adf4ba8dc1decca2e3ea"
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
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=node, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=node, text=True)
    if commit != ZENON or dirty:
        raise ValueError("Expected a clean checkout at the pinned devnet node revision")
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
    # Match the gnark replacement declared by the pinned Ziren wrapper.
    subprocess.run([
        "go", "mod", "edit",
        "-require=github.com/consensys/gnark@v0.10.1-0.20240504023521-d9bfacd7cb60",
        "-require=github.com/consensys/gnark-crypto@v0.14.0",
        "-replace=github.com/consensys/gnark=github.com/jtguibas/gnark@v0.0.0-20240923234830-41125bc1909c",
    ], cwd=node, check=True, timeout=15)
