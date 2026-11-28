#!/usr/bin/env bash
set -euo pipefail

work_dir="$1"
archive_name=bitcoin-32.1-x86_64-linux-gnu.tar.gz
archive="${work_dir}/${archive_name}"
archive_size=93921790
# https://bitcoincore.org/bin/bitcoin-core-32.1/SHA256SUMS
archive_sha256=61545c17c3120fb7412f896d248b11993af04ec8817aad3649c55b2afb044963

curl --disable --fail --silent --show-error --proto '=https' \
  --connect-timeout 10 --max-time 120 --max-filesize "$archive_size" \
  --output "$archive" "https://bitcoincore.org/bin/bitcoin-core-32.1/${archive_name}"
test "$(stat --format=%s "$archive")" = "$archive_size"
printf '%s  %s\n' "$archive_sha256" "$archive" | sha256sum --check --strict

python3 -B - "$archive" "${work_dir}/bitcoind" <<'PY'
from pathlib import Path
import shutil
import sys
import tarfile

archive, binary = map(Path, sys.argv[1:])
with tarfile.open(archive, "r:gz") as release:
    member = release.getmember("bitcoin-32.1/bin/bitcoind")
    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or not 0 < member.size <= 100 * 1024 * 1024:
        raise SystemExit("Expected a bounded regular bitcoind archive member")
    with release.extractfile(member) as source, binary.open("xb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
binary.chmod(0o700)
archive.unlink()
PY
