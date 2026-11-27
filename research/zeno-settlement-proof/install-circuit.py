"""Stream the pinned Ziren archive into disposable hosted CI storage."""

import gzip
import hashlib
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen


URL = "https://zkm-toolchain.s3.us-west-2.amazonaws.com/v2.0.0-groth16.tar.gz"
ETAG = '"28b1550007090ddb26232c7224854666-1391"'
ARCHIVE_BYTES = 11_660_948_984
ARCHIVE_SHA256 = "d8fe235382c59306fc663182b07ae736abcf8c2bcec19a54af565dbe6f032d29"
MAX_UNPACKED = 26 * 1024**3
MAX_MEMBER_COUNT = 64
VK_BYTES = 428
VK_HASH = "b3e14fe98042c3ff6f219172965bddd8dda586c556adac9b4acbd285e13d3cdb"
FILES = {"groth16_vk.bin", "groth16_pk.bin", "groth16_circuit.bin", "constraints.json"}
FREE_SPACE_FLOOR = 10 * 1024**3


class PinnedStream:
    def __init__(self, response):
        self.response = response
        self.remaining = ARCHIVE_BYTES
        self.digest = hashlib.sha256()

    def read(self, size):
        if not 0 <= size <= 1024**2:
            raise ValueError("Invalid archive read size")
        data = self.response.read(min(self.remaining, size))
        if not data and size and self.remaining:
            raise EOFError("Circuit archive transport ended early")
        self.remaining -= len(data)
        self.digest.update(data)
        return data


def extract(response, destination):
    if response.geturl() != URL or response.status != 200:
        raise ValueError("Unexpected circuit archive source")
    if response.headers.get("ETag") != ETAG or int(response.headers["Content-Length"]) != ARCHIVE_BYTES:
        raise ValueError("Circuit archive version or size differs")
    stream = PinnedStream(response)
    seen = set()
    expanded = 0
    with gzip.GzipFile(fileobj=stream) as uncompressed:
        with tarfile.open(fileobj=uncompressed, mode="r|") as archive:
            for index, member in enumerate(archive):
                path = PurePosixPath(member.name)
                if index >= MAX_MEMBER_COUNT or path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unexpected circuit archive path or member count")
                if member.isdir():
                    continue
                if not member.isreg() or member.size <= 0:
                    raise ValueError("Unexpected circuit archive member type or size")
                expanded += member.size
                if expanded > MAX_UNPACKED:
                    raise ValueError("Circuit archive exceeds its expansion limit")
                if path.name not in FILES:
                    continue
                if path.name in seen:
                    raise ValueError("Duplicate circuit archive member")
                if path.name == "groth16_vk.bin" and member.size != VK_BYTES:
                    raise ValueError("Unexpected circuit verifying key size")
                if shutil.disk_usage(destination).free < FREE_SPACE_FLOOR + member.size:
                    raise ValueError("Insufficient disk headroom for a circuit member")
                with archive.extractfile(member) as source, (destination / path.name).open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024**2)
                seen.add(path.name)
                print(f"SETTLEMENT_CIRCUIT_MEMBER {path.name} {member.size}", flush=True)
        # Require the gzip trailer, including when tar ends before it.
        while chunk := uncompressed.read(1024**2):
            if any(chunk):
                raise ValueError("Unexpected trailing circuit archive content")
    if stream.remaining or seen != FILES:
        raise ValueError("Incomplete circuit archive")
    archive_sha = stream.digest.hexdigest()
    if archive_sha != ARCHIVE_SHA256:
        raise ValueError("Circuit archive SHA-256 differs")
    vk = (destination / "groth16_vk.bin").read_bytes()
    if hashlib.sha256(vk).hexdigest() != VK_HASH:
        raise ValueError("Circuit key differs from the source-pinned Ziren verifier")
    print(f"SETTLEMENT_CIRCUIT_ARCHIVE_SHA256 {archive_sha}", flush=True)


if __name__ == "__main__":
    target = Path(sys.argv[1])
    target.mkdir(mode=0o700)
    with urlopen(Request(URL, headers={"If-Match": ETAG}), timeout=60) as archive_response:
        extract(archive_response, target)
