#!/usr/bin/env python3
"""Extract the UEFI drivers and raw files of the Pixel 3 XBL into the Mu-Silicium Device-Binaries layout.

Input: xbl.img (an ELF; the segment loaded at the UEFI FD base holds the firmware volume).
Output: <out>/<Package>/<Path>/<Name>/<Name>.efi, .depex and .inf, as in Project-Silicium/Device-Binaries,
plus <out>/RawFiles/<UI name> for FREEFORM/RAW files (uefiplat.cfg, SecParti.cfg, ...) and a manifest.

Only Python's standard library is used: FV/FFS/section parsing per the PI specification, LZMA
(EE4E5898-...) GUIDed sections through lzma FORMAT_ALONE, Qualcomm zlib (1D301FE9-...) through zlib.
"""

import argparse
import hashlib
import json
import lzma
import struct
import uuid
import zlib
from pathlib import Path

LZMA_GUID = uuid.UUID("EE4E5898-3914-4259-9D6E-DC7BD79403CF")
ZLIB_GUID = uuid.UUID("1D301FE9-BE79-4353-91C2-D23BC959AE0C")   # Qualcomm XBL: gzip-compressed FV
FFS_TYPES = {0x01: "RAW", 0x02: "FREEFORM", 0x03: "SECURITY_CORE", 0x04: "PEI_CORE", 0x05: "DXE_CORE",
             0x06: "PEIM", 0x07: "DRIVER", 0x08: "COMBINED_PEIM_DRIVER", 0x09: "APPLICATION", 0x0A: "SMM",
             0x0B: "FIRMWARE_VOLUME_IMAGE", 0xF0: "FFS_PAD"}
MODULE_TYPES = {"DRIVER": "DXE_DRIVER", "DXE_CORE": "DXE_CORE", "APPLICATION": "UEFI_APPLICATION",
                "SECURITY_CORE": "SEC", "PEI_CORE": "PEI_CORE", "PEIM": "PEIM"}
SEC_COMPRESSION, SEC_GUID_DEFINED, SEC_PE32, SEC_TE, SEC_DXE_DEPEX, SEC_UI, SEC_FV_IMAGE, SEC_RAW = \
    0x01, 0x02, 0x10, 0x12, 0x13, 0x15, 0x17, 0x19


def guid_at(b, o):
    return uuid.UUID(bytes_le=bytes(b[o:o + 16]))


def sections(buf):
    """Yield (type, payload) for every leaf section, descending into GUIDed and FV image sections."""
    o = 0
    while o + 4 <= len(buf):
        size = int.from_bytes(buf[o:o + 3], "little")
        stype = buf[o + 3]
        hdr = 4
        if size == 0xFFFFFF:
            size = struct.unpack_from("<I", buf, o + 4)[0]
            hdr = 8
        if size < hdr:
            break
        body = buf[o + hdr:o + size]
        if stype == SEC_GUID_DEFINED:
            g = guid_at(body, 0)
            data_off, attrs = struct.unpack_from("<HH", body, 16)
            inner = body[data_off - hdr:]
            if g == LZMA_GUID:
                inner = lzma.decompress(bytes(inner), format=lzma.FORMAT_ALONE)
            elif g == ZLIB_GUID:
                inner = zlib.decompress(bytes(inner), wbits=47)   # gzip or zlib header
            elif attrs & 1:
                raise ValueError(f"unsupported GUIDed section {g}")
            yield from sections(inner)
        elif stype == SEC_COMPRESSION:
            raise ValueError("EFI_COMPRESSION_SECTION is not supported")
        elif stype == SEC_FV_IMAGE:
            yield SEC_FV_IMAGE, body
        else:
            yield stype, body
        o += (size + 3) & ~3


def volume_files(fv):
    """Yield (guid, ffs type name, section list) for each file of a firmware volume."""
    if fv[40:44] != b"_FVH":
        raise ValueError("not a firmware volume")
    fv_len = struct.unpack_from("<Q", fv, 32)[0]
    hdr_len = struct.unpack_from("<H", fv, 48)[0]
    ext_off = struct.unpack_from("<H", fv, 52)[0]
    o = hdr_len
    if ext_off:
        o = ext_off + struct.unpack_from("<I", fv, ext_off + 16)[0]
    o = (o + 7) & ~7
    while o + 24 <= fv_len:
        if fv[o:o + 24] == b"\xff" * 24:
            break
        g = guid_at(fv, o)
        ftype = fv[o + 18]
        attrs = fv[o + 19]
        size = int.from_bytes(fv[o + 20:o + 23], "little")
        hdr = 24
        if attrs & 0x01 and size == 0:  # FFS_ATTRIB_LARGE_FILE
            size = struct.unpack_from("<Q", fv, o + 24)[0]
            hdr = 32
        body = fv[o + hdr:o + size]
        name = FFS_TYPES.get(ftype, f"0x{ftype:02x}")
        if name != "FFS_PAD":
            if name == "RAW":
                yield g, name, [(SEC_RAW, body)]
            else:
                secs = list(sections(body))
                yield g, name, secs
                for st, payload in secs:
                    if st == SEC_FV_IMAGE:
                        yield from volume_files(payload)
        o = (o + size + 7) & ~7


def ui_name(secs):
    for st, b in secs:
        if st == SEC_UI:
            return bytes(b).decode("utf-16-le").rstrip("\0")
    return None


def find_fv(xbl, fd_base):
    e_phoff = struct.unpack_from("<Q", xbl, 0x20)[0]
    for k in range(struct.unpack_from("<H", xbl, 0x38)[0]):
        p_type, _, off, _, pa, fsz, _, _ = struct.unpack_from("<IIQQQQQQ", xbl, e_phoff + k * 56)
        if p_type == 1 and pa == fd_base:
            seg = xbl[off:off + fsz]
            i = seg.find(b"_FVH")
            return seg[i - 40:], fsz
    raise SystemExit(f"no ELF segment at 0x{fd_base:x}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xbl", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--fd-base", type=lambda s: int(s, 0), default=0x9FC00000)
    ap.add_argument("--package-map", type=Path, help="JSON {UI name: 'Pkg/Path'}; default: QcomPkg/Drivers")
    args = ap.parse_args()

    xbl = args.xbl.read_bytes()
    fv, seg_size = find_fv(xbl, args.fd_base)
    pkg_map = json.loads(args.package_map.read_text()) if args.package_map else {}
    manifest = {"source": args.xbl.name, "source_sha256": hashlib.sha256(xbl).hexdigest(),
                "fd_base": hex(args.fd_base), "segment_size": hex(seg_size), "files": []}

    for g, ftype, secs in volume_files(fv):
        name = ui_name(secs)
        pe = [b for st, b in secs if st in (SEC_PE32, SEC_TE)]
        depex = [b for st, b in secs if st == SEC_DXE_DEPEX]
        raw = [b for st, b in secs if st == SEC_RAW]
        entry = {"guid": str(g).upper(), "type": ftype, "name": name}
        if pe and name:
            rel = Path(pkg_map.get(name, "QcomPkg/Drivers")) / name
            d = args.out / rel
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name}.efi").write_bytes(bytes(pe[0]))
            lines = ["[Defines]", "  INF_VERSION                    = 0x0001001B",
                     f"  BASE_NAME                      = {name}",
                     f"  FILE_GUID                      = {str(g).upper()}",
                     f"  MODULE_TYPE                    = {MODULE_TYPES.get(ftype, 'DXE_DRIVER')}",
                     "  VERSION_STRING                 = 1.0", "  ENTRY_POINT                    = EfiEntry", "",
                     "[Binaries.AARCH64]"]
            if depex:
                (d / f"{name}.depex").write_bytes(bytes(depex[0]))
                lines.append(f"  DXE_DEPEX|{name}.depex|*")
            lines += [f"  PE32|{name}.efi|*", "", "[Depex]", "  TRUE", ""]
            (d / f"{name}.inf").write_text("\n".join(lines), newline="\n")
            entry.update(path=str(rel).replace("\\", "/"), sha256=hashlib.sha256(pe[0]).hexdigest(),
                         depex=bool(depex))
        elif raw and ftype in ("FREEFORM", "RAW"):
            fname = name or str(g).upper()
            d = args.out / "RawFiles"
            d.mkdir(parents=True, exist_ok=True)
            (d / fname).write_bytes(bytes(raw[0]))
            entry.update(path=f"RawFiles/{fname}", sha256=hashlib.sha256(raw[0]).hexdigest(), size=len(raw[0]))
        manifest["files"].append(entry)

    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", newline="\n")
    for f in manifest["files"]:
        print(f"{f['type']:22} {f['name'] or '-':32} {f['guid']}  {f.get('path', '')}")


if __name__ == "__main__":
    main()
