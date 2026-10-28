#!/usr/bin/env python3
"""Rebuild the UniXNU Data volume the phone wrote (seed + persistent overlay) as a raw image, on the build Mac.

    blueline-data-rebuild.py SEED.dbr --bitmap data-bitmap.bin --pages data-pages.bin -o data.img
    hdiutil attach -readonly -nomount -imagekey diskimage-class=CRawDiskImage data.img

SEED is the packed seed (DBRDISK1, drivers/RamBlock/SparseDisk.h of UniXNU): its nonzero pages go to their logical
offsets. The overlay (drivers/RamBlock/PersistOverlay.h) starts at LUN 0 LBA 0x98347f: a header block, a 640-block
bitmap (bit i, least significant first, = logical page i written), then logical page i at store block 1 + 640 + i.
Read it from TWRP without anything on the phone but dd (Windows cmd; the run list is made locally, Git Bash):

    adb exec-out "dd if=/dev/block/sda bs=4096 skip=9974912 count=640 2>/dev/null" > data-bitmap.bin
    xxd -p -c 1 data-bitmap.bin | awk '{v=strtonum("0x"$1); for(k=0;k<8;k++) if(int(v/2^k)%2){i=(NR-1)*8+k;
        if(n&&i==s+n)n++; else{if(n)print s,n; s=i;n=1}}} END{if(n)print s,n}' > runs.txt
    adb push runs.txt /tmp/runs.txt
    adb exec-out "while read p n; do dd if=/dev/block/sda bs=4096 skip=$((9975552 + p)) count=$n 2>/dev/null; done < /tmp/runs.txt" > data-pages.bin

The pages file then holds exactly the marked pages in page order. Without --bitmap/--pages this writes the seed alone
(a check of the tool). The image is sparse.
"""
import argparse
import struct
from pathlib import Path

PAGE = 4096


def seed_extents(blob):
    if blob[:8] != b'DBRDISK1' or struct.unpack_from('<II', blob, 8) != (1, PAGE):
        raise SystemExit('not a packed DBRDISK1 seed')
    size, count = struct.unpack_from('<QQ', blob, 16)
    cursor = 32 + count * 16
    for i in range(count):
        at, length = struct.unpack_from('<QQ', blob, 32 + i * 16)
        yield at, blob[cursor:cursor + length]
        cursor += length
    if cursor != len(blob):
        raise SystemExit('seed extents do not cover the file')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('seed', type=Path)
    ap.add_argument('--bitmap', type=Path)
    ap.add_argument('--pages', type=Path)
    ap.add_argument('-o', '--output', type=Path, required=True)
    a = ap.parse_args()
    blob = a.seed.read_bytes()
    size = struct.unpack_from('<Q', blob, 16)[0]
    with open(a.output, 'wb') as out:
        out.truncate(size)
        seeded = 0
        for at, data in seed_extents(blob):
            out.seek(at)
            out.write(data)
            seeded += len(data) // PAGE
        print(f'seed: {size / 2**30:.0f} GiB logical, {seeded} pages from {len(blob)} bytes')
        if a.bitmap or a.pages:
            bitmap = a.bitmap.read_bytes()
            pages = a.pages.stat().st_size
            if len(bitmap) != 640 * PAGE or pages % PAGE:
                raise SystemExit(f'bitmap is {len(bitmap)} bytes (want {640 * PAGE}); pages file {pages} bytes')
            indices = [byte * 8 + k for byte, b in enumerate(bitmap) if b for k in range(8) if b >> k & 1]
            if indices and indices[-1] >= size // PAGE:
                raise SystemExit('bitmap marks pages past the logical size')
            if pages != len(indices) * PAGE:
                raise SystemExit(f'pages file holds {pages // PAGE} pages, the bitmap marks {len(indices)}')
            with open(a.pages, 'rb') as src:
                for index in indices:
                    out.seek(index * PAGE)
                    out.write(src.read(PAGE))
            print(f'overlay: {len(indices)} pages ({len(indices) * PAGE / 2**20:.1f} MiB), '
                  f'logical pages {indices[0] if indices else "-"}..{indices[-1] if indices else "-"}')
    print(f'wrote {a.output}')


if __name__ == '__main__':
    main()
