"""Transport only cold chain databases between the disposable devnet jobs."""

import hashlib
import io
import json
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path


PROJECT = "zeno-devnet"
ROLES = ("pillar", "pillar2", "pillar3", "rpc", "observer")
DATABASES = ("nom", "consensus", "cache", "statetree")
LEVELDB_FILE = re.compile(r"CURRENT(?:\.bak)?|MANIFEST-[0-9]+|[0-9]+\.(?:log|ldb|sst)")
MAX_BYTES = 512 * 1024**2
MAX_FILES = 4096
CHUNK = 1024**2
FREE_FLOOR = 10 * 1024**3


def docker_inspect(*arguments):
    [result] = json.loads(subprocess.check_output(["docker", *arguments], timeout=15))
    return result


def owned_volumes(mode):
    roots = {}
    for role in ROLES:
        container = docker_inspect("inspect", f"znnd-devnet-{role}")
        labels = container["Config"]["Labels"]
        state = container["State"]
        if labels["com.docker.compose.project"] != PROJECT or labels["com.docker.compose.service"] != role:
            raise ValueError(f"Unexpected container owner for {role}")
        if mode == "save" and (state["Status"], state["ExitCode"]) != ("exited", 0):
            raise ValueError(f"Expected a cleanly stopped devnet node for {role}")
        if mode == "restore" and state["Status"] != "created":
            raise ValueError(f"Expected a fresh devnet container for {role}")
        [mount] = [entry for entry in container["Mounts"] if entry["Destination"] == "/root/.znn"]
        if mount["Type"] != "volume" or mount["Name"] != f"{PROJECT}_{role}-data":
            raise ValueError(f"Unexpected devnet volume for {role}")
        volume = docker_inspect("volume", "inspect", mount["Name"])
        if volume["Driver"] != "local" or volume["Labels"]["com.docker.compose.project"] != PROJECT:
            raise ValueError(f"Unexpected storage owner for {role}")
        root = Path(volume["Mountpoint"])
        if not root.is_dir() or root.resolve(strict=True) != root or root != Path(mount["Source"]):
            raise ValueError(f"Unexpected volume identity for {role}")
        if mode == "restore" and any(root.iterdir()):
            raise ValueError(f"Restore requires an empty volume for {role}")
        roots[role] = root
    return roots


def hash_file(path, size):
    with path.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256")
    if path.lstat().st_size != size:
        raise ValueError("A cold database file changed during hashing")
    return digest.hexdigest()


def manifest_for(roots):
    entries = []
    objects = {}
    total = 0
    for role, root in roots.items():
        for database in DATABASES:
            directory = root / database
            if not stat.S_ISDIR(directory.lstat().st_mode):
                raise ValueError(f"Expected a data directory for {role}/{database}")
            files = sorted(directory.iterdir())
            names = {path.name for path in files}
            if "CURRENT" not in names or not any(name.startswith("MANIFEST-") for name in names):
                raise ValueError(f"Missing LevelDB metadata in {role}/{database}")
            for path in files:
                st = path.lstat()
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    raise ValueError(f"Unexpected database member in {role}/{database}")
                if path.name in {"LOCK", "LOG", "LOG.old"}:
                    continue
                if LEVELDB_FILE.fullmatch(path.name) is None:
                    raise ValueError(f"Unexpected LevelDB data filename in {role}/{database}: {path.name}")
                total += st.st_size
                if total > MAX_BYTES or len(entries) >= MAX_FILES:
                    raise ValueError("Devnet checkpoint exceeded its allocation")
                digest = hash_file(path, st.st_size)
                entries.append({"role": role, "database": database, "file": path.name, "size": st.st_size, "sha256": digest})
                objects.setdefault(digest, (path, st.st_size))
    return entries, objects


def save(target, roots):
    entries, objects = manifest_for(roots)
    max_size = sum(size for _, size in objects.values()) + (len(objects) + 22) * 2048
    if shutil.disk_usage(target.parent).free < FREE_FLOOR + max_size:
        raise ValueError("Insufficient free space for the cold checkpoint")
    manifest = json.dumps({"schema": 1, "entries": entries}, separators=(",", ":")).encode()
    with tarfile.open(target, "x", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo("checkpoint.json")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
        for digest, (path, size) in sorted(objects.items()):
            info = tarfile.TarInfo("objects/" + digest)
            info.size = size
            with path.open("rb") as source:
                archive.addfile(info, source)
    print(f"VALIDITY_CHECKPOINT {len(entries)} files {len(objects)} objects {target.stat().st_size} bytes")


def paths_from_manifest(value, roots):
    if value["schema"] != 1 or not 0 < len(value["entries"]) <= MAX_FILES:
        raise ValueError("Invalid checkpoint manifest")
    objects = defaultdict(list)
    seen = set()
    total = 0
    for record in value["entries"]:
        role, database, name = record["role"], record["database"], record["file"]
        size, digest = record["size"], record["sha256"]
        if (role not in roots or database not in DATABASES or LEVELDB_FILE.fullmatch(name) is None or
                not isinstance(size, int) or not 0 <= size <= MAX_BYTES or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
            raise ValueError("Invalid manifest file reference")
        relative = (role, database, name)
        if relative in seen:
            raise ValueError("Duplicate cold checkpoint file reference")
        seen.add(relative)
        total += size
        objects[digest].append((roots[role] / database / name, size))
    if total > MAX_BYTES or any(shutil.disk_usage(root).free < FREE_FLOOR + total for root in roots.values()):
        raise ValueError("Insufficient allocation for the restored files")
    for role in roots:
        for database in DATABASES:
            if (role, database, "CURRENT") not in seen:
                raise ValueError("Incomplete cold checkpoint database list")
    return objects


def restore(target, roots):
    if target.stat().st_size > MAX_BYTES + 16 * 1024**2:
        raise ValueError("Cold checkpoint archive exceeds its bound")
    with tarfile.open(target, "r|") as archive:
        first = archive.next()
        if first is None or first.name != "checkpoint.json" or not first.isfile() or first.size > 1024**2:
            raise ValueError("Missing bounded cold checkpoint manifest")
        with archive.extractfile(first) as manifest:
            objects = paths_from_manifest(json.load(manifest), roots)
        for root in roots.values():
            for database in DATABASES:
                (root / database).mkdir(mode=0o700)
        while member := archive.next():
            if not member.isfile() or not member.name.startswith("objects/"):
                raise ValueError("Unexpected checkpoint member")
            digest = member.name.removeprefix("objects/")
            paths = objects.pop(digest, None)
            if paths is None or any(size != member.size for _, size in paths):
                raise ValueError("Unexpected checkpoint object or size")
            actual = hashlib.sha256()
            with ExitStack() as stack:
                source = stack.enter_context(archive.extractfile(member))
                writers = [stack.enter_context(path.open("xb")) for path, _ in paths]
                while chunk := source.read(CHUNK):
                    actual.update(chunk)
                    for output in writers:
                        output.write(chunk)
            if actual.hexdigest() != digest:
                raise ValueError("Cold checkpoint object digest differs")
        if objects:
            raise ValueError("Incomplete cold checkpoint archive")
    print("VALIDITY_CHECKPOINT_DATABASES_VERIFIED")


if __name__ == "__main__":
    mode, file = sys.argv[1:]
    if mode not in {"save", "restore"} or sys.platform != "linux":
        raise SystemExit("Expected a cold checkpoint operation on the disposable Linux devnet")
    roots = owned_volumes(mode)
    (save if mode == "save" else restore)(Path(file), roots)
