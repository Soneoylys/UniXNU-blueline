#!/usr/bin/env python3
"""Stage the Pixel 3 vendor inputs into a Mu-Silicium checkout for the bluelinePkg build.

    python scripts/import-blueline-uefi.py ../uefi-blueline

Inputs (not tracked; produced by scripts/fetch-blueline-factory.py and scripts/extract-blueline-xbl.py):
  .cache/firmware/xbl-extract/        the stock XBL DXE drivers and raw files
  .cache/firmware/boot.img            the stock boot image; its first DTB (SDM845 v2.1, msm-id 0x141/0x20001)

Outputs in the checkout:
  Binaries/blueline/                  the drivers, in the Device-Binaries layout bluelinePkg's DXE.inc names
  Resources/DTBs/blueline.dtb         the DTB appended to the boot image, which the stock ABL checks
"""

import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "firmware"

# Pinned inputs: bootloader b1c1-0.4-7617406 and boot.img of blueline SP1A.210812.016.C2.
BOOTLOADER = ("bootloader-blueline-b1c1-0.4-7617406.img", "85a788ecb531b4d7351e7c867159c29bd40d1ad849425040e18c59cfb90d5dc6")
BOOT_IMG = ("boot.img", "5c1ce19e814e2f198baa877a7351306079272fe67e13404e31f3bab213bb506e")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name, digest):
    path = CACHE / name
    if not path.is_file():
        sys.exit(f"{path} missing: run scripts/fetch-blueline-factory.py")
    if sha256(path) != digest:
        sys.exit(f"{path}: sha256 differs from the pinned {digest}")
    return path


def stock_dtb(boot):
    """First DTB of the boot image v2 dtb section."""
    d = boot.read_bytes()
    if d[:8] != b"ANDROID!" or struct.unpack_from("<I", d, 40)[0] != 2:
        sys.exit("boot.img is not a header v2 Android boot image")
    page = struct.unpack_from("<I", d, 36)[0]
    kernel, ramdisk, second = (struct.unpack_from("<I", d, o)[0] for o in (8, 16, 24))
    recovery_dtbo = struct.unpack_from("<I", d, 1632)[0]
    dtb_size = struct.unpack_from("<I", d, 1648)[0]
    pages = lambda n: -(-n // page)
    off = page * (1 + pages(kernel) + pages(ramdisk) + pages(second) + pages(recovery_dtbo))
    blob = d[off:off + dtb_size]
    if blob[:4] != b"\xd0\x0d\xfe\xed":
        sys.exit("boot.img dtb section does not start with an FDT")
    return blob[:struct.unpack_from(">I", blob, 4)[0]]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    uefi = Path(sys.argv[1]).resolve()
    if not (uefi / "Platforms/Google/bluelinePkg/blueline.dsc").is_file():
        sys.exit(f"{uefi} has no Platforms/Google/bluelinePkg")
    check(*BOOTLOADER)
    boot = check(*BOOT_IMG)
    extract = CACHE / "xbl-extract"
    manifest = json.loads((extract / "manifest.json").read_text())
    xbl = CACHE / "bootloader" / "xbl.img"
    if not xbl.is_file() or sha256(xbl) != manifest["source_sha256"]:
        sys.exit("xbl-extract does not match bootloader/xbl.img: rerun scripts/extract-blueline-xbl.py")

    dest = uefi / "Binaries" / "blueline"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(extract, dest)
    dtb = stock_dtb(boot)
    (uefi / "Resources/DTBs/blueline.dtb").write_bytes(dtb)
    print(f"{dest}: {sum(1 for _ in dest.rglob('*.efi'))} drivers")
    print(f"{uefi / 'Resources/DTBs/blueline.dtb'}: {len(dtb)} bytes, sha256 {hashlib.sha256(dtb).hexdigest()}")


if __name__ == "__main__":
    main()
