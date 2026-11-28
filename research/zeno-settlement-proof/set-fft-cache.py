"""Select gnark-crypto's on-demand FFT mode for the disposable PK copy."""

import sys
from pathlib import Path


PROVING_KEY_BYTES = 11_220_021_241
DUMP_MARKER = (0xDEADBEEF).to_bytes(8, "little")
# WriteDump writes the marker then the FFT domain, which consists of the
# cardinality, five serialized scalars and the precompute flag.
CACHE_OFFSET = 8 + 8 + 5 * 32


if __name__ == "__main__":
    path = Path(sys.argv[1])
    if path.stat().st_size != PROVING_KEY_BYTES:
        raise ValueError("Unexpected v2.0.0 proving key size")
    with path.open("r+b") as key:
        header = key.read(CACHE_OFFSET + 1)
        cardinality = int.from_bytes(header[8:16], "big")
        if (header[:8] != DUMP_MARKER or not cardinality or cardinality & (cardinality - 1)
                or header[CACHE_OFFSET:] not in (b"\x00", b"\x01")):
            raise ValueError("Unexpected v2.0.0 proving key FFT domain encoding")
        key.seek(CACHE_OFFSET)
        key.write(b"\x00")
    print(f"SETTLEMENT_FFT_CACHE on-demand cardinality={cardinality} original={header[-1]}")
