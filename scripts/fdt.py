#!/usr/bin/env python3
"""Minimal flattened device tree reader (no dtc on Windows): nodes, properties, phandles.

    python scripts/fdt.py FILE.dtb [/path/prefix ...]       print nodes and properties under the prefixes
    python scripts/fdt.py --dtbo dtbo.img                   list the overlays of an Android dtbo image
"""

import struct
import sys
from pathlib import Path

FDT_MAGIC = 0xD00DFEED
DT_TABLE_MAGIC = 0xD7B7AB1E


class Node:
    def __init__(self, name, parent=None):
        self.name, self.parent, self.props, self.children = name, parent, {}, []

    @property
    def path(self):
        if self.parent is None:
            return "/"
        p = self.parent.path
        return (p if p.endswith("/") else p + "/") + self.name

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def u32s(self, name):
        v = self.props.get(name, b"")
        return list(struct.unpack(f">{len(v) // 4}I", v))

    def string(self, name):
        return self.props.get(name, b"").rstrip(b"\0").decode(errors="replace")


def parse(blob):
    magic, total, off_struct, off_strings = struct.unpack_from(">IIII", blob, 0)
    if magic != FDT_MAGIC:
        raise ValueError("not an FDT")
    p, root, cur = off_struct, None, None
    while True:
        tok = struct.unpack_from(">I", blob, p)[0]
        p += 4
        if tok == 1:
            end = blob.index(b"\0", p)
            node = Node(blob[p:end].decode(), cur)
            if cur is None:
                root = node
            else:
                cur.children.append(node)
            cur = node
            p = (end + 4) & ~3
        elif tok == 2:
            cur = cur.parent
        elif tok == 3:
            ln, name_off = struct.unpack_from(">II", blob, p)
            p += 8
            name = blob[off_strings + name_off:blob.index(b"\0", off_strings + name_off)].decode()
            cur.props[name] = bytes(blob[p:p + ln])
            p = (p + ln + 3) & ~3
        elif tok == 4:
            continue
        elif tok == 9:
            return root
        else:
            raise ValueError(f"bad token {tok:#x} at {p - 4:#x}")


def dtbo_entries(blob):
    """Entries of an Android DT table image: (dt bytes, id, rev, custom words)."""
    magic, total, header_size, entry_size, count, entries_offset = struct.unpack_from(">IIIIII", blob, 0)
    if magic != DT_TABLE_MAGIC:
        raise ValueError("not a dtbo image")
    for i in range(count):
        size, offset, ident, rev, *custom = struct.unpack_from(">IIII4I", blob, entries_offset + i * entry_size)
        yield blob[offset:offset + size], ident, rev, custom


def fmt(value):
    if value and value[-1:] == b"\0" and all(32 <= c < 127 or c == 0 for c in value[:-1]) and value[:1] != b"\0":
        return repr(value.rstrip(b"\0").decode().replace("\0", '", "'))
    if len(value) % 4 == 0 and value:
        return "<" + " ".join(f"{x:#x}" for x in struct.unpack(f">{len(value) // 4}I", value)) + ">"
    return value.hex()


def main():
    args = sys.argv[1:]
    if args[:1] == ["--dtbo"]:
        for n, (dt, ident, rev, custom) in enumerate(dtbo_entries(Path(args[1]).read_bytes())):
            root = parse(dt)
            print(n, f"id={ident:#x} rev={rev:#x}", root.string("model"), "board-id", [hex(x) for x in root.u32s("qcom,board-id")])
        return
    root = parse(Path(args[0]).read_bytes())
    prefixes = args[1:] or ["/"]
    for node in root.walk():
        if any(node.path.startswith(p) for p in prefixes):
            print(node.path)
            for k, v in node.props.items():
                print(f"    {k} = {fmt(v)}")


if __name__ == "__main__":
    main()
