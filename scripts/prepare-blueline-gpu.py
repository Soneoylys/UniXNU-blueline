#!/usr/bin/env python3
"""Extract and verify the pinned A630 firmware from Pixel 3 vendor.img.

Fetch first with scripts/fetch-blueline-factory.py 'image-*.zip/vendor.img'.
Only ignored build/cache files are written; existing mismatched firmware is
refused. No phone or persistent Data store is accessed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def exact(f, n):
    b = f.read(n)
    if len(b) != n:
        raise ValueError('truncated Android sparse image')
    return b


def unsparse(src, dst, expected_bytes):
    with src.open('rb') as f, dst.open('wb') as out:
        magic, major, minor, fsz, csz, block, blocks, chunks, checksum = struct.unpack('<IHHHHIIII', exact(f, 28))
        if magic != 0xed26ff3a or major != 1 or fsz < 28 or csz < 12 or not block or block % 4:
            raise ValueError('invalid Android sparse header')
        if block * blocks != expected_bytes:
            raise ValueError('vendor geometry differs from firmware pin')
        exact(f, fsz - 28)
        total = 0
        for _ in range(chunks):
            kind, reserved, count, size = struct.unpack('<HHII', exact(f, 12))
            exact(f, csz - 12)
            n = count * block
            if total + n > expected_bytes:
                raise ValueError('sparse chunk exceeds vendor geometry')
            if kind == 0xcac1:
                if size != csz + n:
                    raise ValueError('invalid raw chunk size')
                left = n
                while left:
                    b = exact(f, min(left, 1 << 20))
                    out.write(b)
                    left -= len(b)
            elif kind == 0xcac2:
                if size != csz + 4:
                    raise ValueError('invalid fill chunk size')
                pattern = exact(f, 4) * ((1 << 20) // 4)
                left = n
                while left:
                    amount = min(left, len(pattern))
                    out.write(pattern[:amount])
                    left -= amount
            elif kind == 0xcac3:
                if size != csz:
                    raise ValueError('invalid empty chunk size')
                out.seek(n, 1)
            elif kind == 0xcac4:
                if count or size != csz + 4:
                    raise ValueError('invalid CRC chunk size')
                exact(f, 4)  # Input SHA-256, rather than this optional CRC, authenticates the file.
            else:
                raise ValueError(f'unsupported sparse chunk {kind:#x}')
            total += n
        if total != expected_bytes or f.read(1):
            raise ValueError('incomplete geometry or trailing sparse input')
        out.truncate(expected_bytes)


def verify(path, record):
    if path.stat().st_size != record['bytes'] or digest(path) != record['sha256']:
        raise ValueError(f'firmware pin mismatch: {record["name"]}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vendor', type=Path, default=ROOT / '.cache/firmware/vendor.img')
    p.add_argument('--output', type=Path, default=ROOT / '.cache/firmware/blueline-gpu/firmware')
    p.add_argument('--7zip', dest='sevenzip', default=shutil.which('7z') or 'C:/Program Files/7-Zip/7z.exe')
    p.add_argument('--verify-only', action='store_true')
    args = p.parse_args()
    pins = json.loads((ROOT / 'firmware/adreno630.json').read_text(encoding='utf-8'))
    if not args.verify_only:
        if digest(args.vendor) != pins['vendor_sparse_sha256']:
            p.error('vendor.img is not the pinned Pixel 3 factory image')
        for record in pins['files']:
            target = args.output / record['name']
            if target.exists():
                verify(target, record)
        work = ROOT / 'build/gpu-reference'
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='extract-', dir=work) as td:
            td = Path(td)
            raw = td / 'vendor.raw'
            unsparse(args.vendor, raw, pins['vendor_raw_bytes'])
            subprocess.run([args.sevenzip, 'x', str(raw), 'firmware/a630*', '-o' + str(td / 'extracted'),
                            '-y', '-bso0', '-bsp0'], check=True)
            for record in pins['files']:
                verify(td / 'extracted/firmware' / record['name'], record)
            args.output.mkdir(parents=True, exist_ok=True)
            for record in pins['files']:
                shutil.copyfile(td / 'extracted/firmware' / record['name'], args.output / record['name'])
    for record in pins['files']:
        verify(args.output / record['name'], record)
    report = ROOT / 'build/gpu-reference/firmware-report.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(dict(device=pins['device'], files=pins['files'], verified=True,
                                     phone_qualified=False), indent=2) + '\n', encoding='utf-8')
    print(f'PASS: {len(pins["files"])} pinned Pixel 3 A630 firmware files')


if __name__ == '__main__':
    main()
