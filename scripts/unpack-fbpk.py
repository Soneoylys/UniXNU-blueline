#!/usr/bin/env python3
"""Unpack a Pixel bootloader image (FBPK v1 container) into its partition images, checking each CRC-32.

    python scripts/unpack-fbpk.py .cache/firmware/bootloader-blueline-b1c1-0.4-7617406.img .cache/firmware/bootloader

Layout (v1): "FBPK", u32 version, char pack_version[68], u32 entry count, u32 total size; then per entry
u32 type (0 partition table, 1 partition image), char name[36], u64 size, u32 offset of the next entry, u32 CRC-32,
followed by the data. Partition tables (FBPT) are written as partition-table-<n>.bin.
"""

import struct
import sys
import zlib
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    data = Path(sys.argv[1]).read_bytes()
    out = Path(sys.argv[2])
    if data[:4] != b"FBPK" or struct.unpack_from("<I", data, 4)[0] != 1:
        sys.exit("not an FBPK v1 image")
    pack_version = data[8:0x4C].split(b"\0")[0].decode()
    count, total = struct.unpack_from("<II", data, 0x4C)
    if total != len(data):
        sys.exit(f"total size {total} differs from the file size {len(data)}")
    out.mkdir(parents=True, exist_ok=True)
    offset, tables = 0x54, 0
    for _ in range(count):
        kind, name, size, following, crc = struct.unpack_from("<I36sQII", data, offset)
        name = name.split(b"\0")[0].decode()
        body = data[offset + 56:offset + 56 + size]
        if zlib.crc32(body) != crc:
            sys.exit(f"{name}: CRC-32 mismatch")
        if kind == 0:
            target = out / f"partition-table-{tables}.bin"
            tables += 1
        else:
            target = out / f"{name}.img"
        target.write_bytes(body)
        print(f"{kind} {name:16} {size:9} -> {target.name}")
        offset = following
    print(f"{pack_version}: {count} entries")


if __name__ == "__main__":
    main()
