#!/usr/bin/env python3
"""Pack a blueline firmware volume into an Android boot image for "fastboot boot" (nothing is flashed).

    python scripts/package-blueline-boot.py --fd build/firmware/blueline-vendor-debug.fd -o build/boot/blueline-vendor.img

Layout, as UniXNU's `unixnu bootimg` and Mu-Silicium's build_uefi.py: gzip(BootShim + FD) with the stock DTB
appended, as the kernel of a header v1 image with the stock Pixel 3 offsets (Resources/Configs/blueline.toml).
An optional ramdisk (the UniXNU payload volume) goes into the ramdisk field.

The ARM64 header of the uefi-sheng BootShim is text_offset = FD_BASE, image_size = shim + FD (the check of
UniXNU's bootimg.py). A non-zero text_offset is also what recent Pixel 3 bootloaders need from a kernel header.
The image is unpacked again and every part compared with its input.
"""

import argparse
import gzip
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LLVM = Path(r"C:\Users\user\edk2-win2\llvm\x\bin")


def tool(name):
    found = shutil.which(name) or (str(LLVM / f"{name}.exe") if (LLVM / f"{name}.exe").is_file() else None)
    if not found:
        sys.exit(f"{name} not found (PATH or {LLVM})")
    return found


def compile_shim(uefi, base, size, scratch):
    obj, binary = scratch / "shim.o", scratch / "shim.bin"
    subprocess.run([tool("clang"), "--target=aarch64-none-elf", "-c", f"-DFD_BASE={base:#x}", f"-DFD_SIZE={size:#x}",
                    "-DREQUIRES_KERNEL_HEADER=1", str(uefi / "BootShim/BootShim.S"), "-o", str(obj)], check=True)
    subprocess.run([tool("llvm-objcopy"), "-O", "binary", str(obj), str(binary)], check=True)
    shim = binary.read_bytes()
    symbols = subprocess.run([tool("llvm-nm"), "-n", str(obj)], check=True, capture_output=True, text=True).stdout
    payload = next((int(line.split()[0], 16) for line in symbols.splitlines() if line.endswith(" _Payload")), None)
    if payload != len(shim):
        sys.exit("a literal pool or padding separates _Payload from the appended firmware volume")
    if shim[56:60] != b"ARM\x64" or struct.unpack_from("<QQ", shim, 8) != (base, len(shim) + size):
        sys.exit("the BootShim ARM64 header is not text_offset = FD_BASE, image_size = shim + FD_SIZE")
    return shim


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uefi", type=Path, default=ROOT / "uefi-blueline")
    ap.add_argument("--fd", type=Path, required=True)
    ap.add_argument("--dtb", type=Path, default=None, help="default: <uefi>/Resources/DTBs/blueline.dtb")
    ap.add_argument("--ramdisk", type=Path, default=None)
    ap.add_argument("--header-v2", action="store_true",
                    help="header v2 with the DTB in its dtb section at 0x1f00000, as the stock Pixel 3 boot image, "
                         "instead of appended to the kernel")
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    config = tomllib.loads((args.uefi / "Resources/Configs/blueline.toml").read_text())
    base, size = config["uefi_fd"]["base"], config["uefi_fd"]["size"]
    image = dict(config["boot_image"])
    if args.header_v2:
        image.update(header_version=2, dtb_offset=0x1F00000)
    firmware = args.fd.read_bytes()
    if len(firmware) != size:
        sys.exit(f"{args.fd} is {len(firmware):#x} bytes, the device config says {size:#x}")
    dtb_path = args.dtb or args.uefi / "Resources/DTBs/blueline.dtb"
    tree = dtb_path.read_bytes()
    if tree[:4] != b"\xd0\x0d\xfe\xed" or struct.unpack_from(">I", tree, 4)[0] != len(tree):
        sys.exit(f"{dtb_path} is not a single compiled device tree")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="blueline-boot-") as name:
        scratch = Path(name)
        shim = compile_shim(args.uefi, base, size, scratch)
        combined = shim + firmware
        appended = b"" if args.header_v2 else tree
        kernel = gzip.compress(combined, mtime=0) + appended
        (scratch / "kernel").write_bytes(kernel)
        ramdisk = args.ramdisk or scratch / "ramdisk"
        if not args.ramdisk:
            ramdisk.write_bytes(b"")
        cmd = [sys.executable, str(args.uefi / "Resources/Scripts/mkbootimg.py"), "--kernel", str(scratch / "kernel"),
               "--ramdisk", str(ramdisk), "-o", str(args.output)]
        if args.header_v2:
            (scratch / "dtb").write_bytes(tree)
            cmd += ["--dtb", str(scratch / "dtb")]
        for key in ("header_version", "base", "kernel_offset", "ramdisk_offset", "tags_offset", "pagesize", "dtb_offset"):
            if key in image:
                cmd += [f"--{key}", str(image[key]) if key in ("header_version", "pagesize") else hex(image[key])]
        subprocess.run(cmd, check=True)
        ramdisk_bytes = ramdisk.read_bytes()

    data = args.output.read_bytes()
    ksize, kaddr, rsize, raddr, ssize, _, tags, page, version = struct.unpack_from("<9I", data, 8)
    if (data[:8] != b"ANDROID!" or version != image["header_version"] or page != image["pagesize"] or ssize
            or kaddr != image["base"] + image["kernel_offset"]
            or raddr != (image["base"] + image["ramdisk_offset"] if rsize else 0)   # mkbootimg: 0 for no ramdisk
            or tags != image["base"] + image["tags_offset"]):
        sys.exit("boot image header does not match the device config")
    unpack = zlib.decompressobj(16 + zlib.MAX_WBITS)
    if unpack.decompress(data[page:page + ksize]) != combined or not unpack.eof or unpack.unused_data != appended:
        sys.exit("gzip, firmware and appended device tree do not round-trip")
    pages = lambda n: -(-n // page)
    start = page * (1 + pages(ksize))
    if data[start:start + rsize] != ramdisk_bytes:
        sys.exit("ramdisk bytes differ")
    if args.header_v2:
        recovery_dtbo, = struct.unpack_from("<I", data, 1632)
        dtb_size, dtb_addr = struct.unpack_from("<IQ", data, 1648)
        dtb_at = page * (1 + pages(ksize) + pages(rsize) + pages(recovery_dtbo))
        if dtb_addr != image["base"] + image["dtb_offset"] or data[dtb_at:dtb_at + dtb_size] != tree:
            sys.exit("header v2 dtb section does not round-trip")
    sha = lambda b: hashlib.sha256(b).hexdigest()
    report = {"image": str(args.output.name), "image_sha256": sha(data), "image_bytes": len(data),
              "fd": str(args.fd.name), "fd_sha256": sha(firmware), "dtb_sha256": sha(tree), "shim_bytes": len(shim),
              "ramdisk_bytes": rsize, "header_version": version, "kernel_address": hex(kaddr), "hardware_tested": False}
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", newline="\n")
    print(f"{args.output}: {len(data)} bytes, sha256 {report['image_sha256']}")


if __name__ == "__main__":
    main()
