#!/usr/bin/env python3
"""Fetch members of a Pixel 3 (blueline) factory image without downloading the whole zip.

The factory zip is read over HTTP range requests; only the central directories and the selected
members are transferred. A pattern "image-*.zip/boot.img" selects a member of the image zip, which
the factory zip stores uncompressed. zipfile checks each member's CRC; the result is written to
.cache/firmware/ with its SHA-256 printed.

    python scripts/fetch-blueline-factory.py "bootloader-*.img" "image-*.zip/boot.img" "image-*.zip/dtbo.img"
"""

import argparse
import fnmatch
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_URL = "https://dl.google.com/dl/android/aosp/blueline-sp1a.210812.016.c2-factory-fa981d87.zip"


class HttpRangeFile(io.RawIOBase):
    def __init__(self, url):
        self.url = url
        self.pos = 0
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req) as r:
            self.size = int(r.headers["Content-Length"])
            if r.headers.get("Accept-Ranges") != "bytes":
                raise SystemExit("server does not accept range requests")
        self.transferred = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{self.pos + n - 1}"})
        with urllib.request.urlopen(req) as r:
            data = r.read()
        self.pos += len(data)
        self.transferred += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)


class SubFile(io.RawIOBase):
    """A window [start, start + size) of a seekable file."""

    def __init__(self, f, start, size):
        self.f, self.start, self.size, self.pos = f, start, size, 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        self.pos = {io.SEEK_SET: offset, io.SEEK_CUR: self.pos + offset, io.SEEK_END: self.size + offset}[whence]
        return self.pos

    def readinto(self, b):
        n = max(0, min(len(b), self.size - self.pos))
        self.f.seek(self.start + self.pos)
        data = self.f.read(n)
        b[: len(data)] = data
        self.pos += len(data)
        return len(data)


def pick(z, pattern):
    hits = [n for n in z.namelist() if fnmatch.fnmatch(Path(n).name, pattern)]
    if len(hits) != 1:
        print("members:", *z.namelist(), sep="\n  ", file=sys.stderr)
        raise SystemExit(f"expected one member matching {pattern}, found {len(hits)}")
    return z.getinfo(hits[0])


def stored_member(z, raw, info):
    """Open a member stored without compression as a zip of its own."""
    if info.compress_type != zipfile.ZIP_STORED:
        raise SystemExit(f"{info.filename} is compressed; nested access needs a stored member")
    raw.seek(info.header_offset)
    header = raw.read(30)
    start = info.header_offset + 30 + int.from_bytes(header[26:28], "little") + int.from_bytes(header[28:30], "little")
    return zipfile.ZipFile(io.BufferedReader(SubFile(raw, start, info.file_size), buffer_size=1 << 20))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("members", nargs="*", default=["bootloader-*.img"], help="member name patterns (default: the bootloader image)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / ".cache" / "firmware")
    args = ap.parse_args()

    f = HttpRangeFile(args.url)
    args.out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BufferedReader(f, buffer_size=1 << 20)) as z:
        for pattern in args.members:
            outer, _, inner = pattern.partition("/")
            info = pick(z, outer)
            if inner:
                with stored_member(z, f, info) as nested:
                    info = pick(nested, inner)
                    data = nested.read(info)
            else:
                data = z.read(info)
            dest = args.out / Path(info.filename).name
            dest.write_bytes(data)
            print(f"{dest}  {len(data)} bytes  sha256 {hashlib.sha256(data).hexdigest()}")
    print(f"zip size {f.size}, transferred {f.transferred} bytes")


if __name__ == "__main__":
    main()
