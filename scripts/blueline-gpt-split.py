#!/usr/bin/env python3
"""Split the Pixel 3's userdata partition: Android keeps userdata (shrunk), the rest becomes a new "darwin" partition.

Works on dumps of UFS LUN 0 (4096-byte logical blocks), taken from a fastboot-booted recovery:

    PRIMARY  the first 34 blocks of /dev/block/sda (protective MBR, GPT header at LBA 1, the entry array)
    BACKUP   the last 34 blocks (the backup entry array and the backup header in the last block)
    --blocks the size of sda in 4096-byte blocks (/sys/block/sda/size / 8)

It checks both headers and their CRCs, finds "userdata", keeps its start, ends it after --userdata-gib, starts --name
on the next --align-kib boundary and ends it on an aligned boundary at or before the last usable LBA. Every other
entry and the disk GUID stay as they are. It writes new PRIMARY/BACKUP blobs (same sizes as the dumps) only after
parsing them again and comparing every entry. Writing them to the phone is a separate, explicit step.
"""
import argparse
import binascii
import struct
import sys
import uuid
from pathlib import Path

BLOCK = 4096
HEADER = struct.Struct('<8sIIIIQQQQ16sQIII')       # 92 bytes
ENTRY = struct.Struct('<16s16sQQQ72s')           # 128 bytes
# The type GUID of the new partition: a Linux filesystem data partition. Android's fs_mgr only mounts partitions it is told
# about, so an unknown name with this type is left alone.
IOS_TYPE = uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4')


def crc(data):
    return binascii.crc32(data) & 0xffffffff


def parse(header_block, entries_blob, label):
    h = HEADER.unpack_from(header_block)
    (sig, rev, size, hcrc, _, current, backup, first, last, guid, entries_lba, count, esize, ecrc) = h
    if sig != b'EFI PART' or size != 92 or esize != 128:
        raise SystemExit(f'{label}: not a GPT header (sig {sig!r}, size {size}, entry size {esize})')
    raw = bytearray(header_block[:92])
    struct.pack_into('<I', raw, 16, 0)
    if crc(bytes(raw)) != hcrc:
        raise SystemExit(f'{label}: header CRC mismatch')
    array = entries_blob[:count * esize]
    if len(array) != count * esize or crc(array) != ecrc:
        raise SystemExit(f'{label}: entry array CRC mismatch')
    entries = [list(ENTRY.unpack_from(array, i * esize)) for i in range(count)]
    return dict(current=current, backup=backup, first=first, last=last, guid=guid, entries_lba=entries_lba,
                count=count, entries=entries)


def name_of(entry):
    return entry[5].decode('utf-16-le').rstrip('\0')


def header_bytes(g, current, backup, entries_lba, array):
    raw = bytearray(HEADER.pack(b'EFI PART', 0x10000, 92, 0, 0, current, backup, g['first'], g['last'], g['guid'],
                                entries_lba, g['count'], 128, crc(array)))
    struct.pack_into('<I', raw, 16, crc(bytes(raw)))
    return bytes(raw) + bytes(BLOCK - 92)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('primary', type=Path)
    ap.add_argument('backup', type=Path)
    ap.add_argument('--blocks', type=int, required=True, help='LUN 0 size in 4096-byte blocks')
    ap.add_argument('--userdata-gib', type=int, default=16)
    ap.add_argument('--align-kib', type=int, default=1024)
    ap.add_argument('--name', default='darwin', help='name of the new partition')
    ap.add_argument('--rename', metavar='OLD=NEW', help='only rename partition OLD to NEW (no split)')
    ap.add_argument('--out', type=Path, help='directory for new-primary.bin and new-backup.bin (omit for a dry run)')
    a = ap.parse_args()
    primary, backup = a.primary.read_bytes(), a.backup.read_bytes()
    if len(primary) % BLOCK or len(backup) % BLOCK:
        raise SystemExit('dumps must be whole 4096-byte blocks')
    p = parse(primary[BLOCK:2 * BLOCK], primary[2 * BLOCK:], 'primary')
    if p['current'] != 1 or p['backup'] != a.blocks - 1 or p['entries_lba'] != 2:
        raise SystemExit(f"primary header LBAs {p['current']}/{p['backup']}/{p['entries_lba']} do not fit a disk of {a.blocks} blocks")
    tail_start = a.blocks - len(backup) // BLOCK          # LBA of the first block of the BACKUP dump
    b_header = backup[-BLOCK:]
    bh = HEADER.unpack_from(b_header)
    b_entries_lba = bh[10]
    b = parse(b_header, backup[(b_entries_lba - tail_start) * BLOCK:], 'backup')
    if b['current'] != a.blocks - 1 or b['backup'] != 1 or b['entries'] != p['entries'] or b['guid'] != p['guid']:
        raise SystemExit('backup GPT does not mirror the primary one')
    names = [name_of(e) for e in p['entries']]
    if a.rename:
        old, new = a.rename.split('=')
        if names.count(old) != 1 or new in names or not 0 < len(new) <= 36:
            raise SystemExit(f'cannot rename {old!r} to {new!r}')
        index = names.index(old)
        entries = [list(e) for e in p['entries']]
        entries[index][5] = new.encode('utf-16-le').ljust(72, b'\0')
        e = entries[index]
        print(f'entry {index}: {old} -> {new}  ({e[2]:#x}..{e[3]:#x}, type and unique GUID kept)')
        return write(a, primary, backup, p, b_entries_lba, tail_start, entries, [index])
    for name in (a.name,):
        if name in names:
            raise SystemExit(f'a "{name}" partition already exists')
    index = names.index('userdata')
    ud = p['entries'][index]
    align = a.align_kib * 1024 // BLOCK
    for i, e in enumerate(p['entries']):
        if i != index and e[0] != bytes(16) and e[2] <= ud[3] and e[3] >= ud[2]:
            raise SystemExit(f'{name_of(e)} overlaps userdata')
    ud_last_new = ud[2] + a.userdata_gib * (1 << 30) // BLOCK - 1
    ios_first = (ud_last_new + 1 + align - 1) // align * align
    ios_last = (min(ud[3], p['last']) + 1) // align * align - 1
    if not ud[2] <= ud_last_new < ios_first <= ios_last <= ud[3]:
        raise SystemExit('userdata is too small for that split')
    entries = [list(e) for e in p['entries']]
    free = [i for i, e in enumerate(entries) if e[0] == bytes(16)]
    if not free:
        # The Pixel 3's LUN 0 table lists exactly its 21 partitions. Its entry arrays still have reserved room: the
        # primary one up to the first usable LBA, the backup one up to the backup header. One more entry is added
        # only when both copies have room for it, so no partition or header moves.
        room_primary = (p['first'] - p['entries_lba']) * BLOCK // 128
        room_backup = (a.blocks - 1 - b_entries_lba) * BLOCK // 128
        if p['count'] + 1 > min(room_primary, room_backup):
            raise SystemExit('no free GPT entry and no room to add one')
        entries.append([bytes(16), bytes(16), 0, 0, 0, bytes(72)])
        p['count'] += 1
        free = [len(entries) - 1]
        print(f'entry array: {p["count"] - 1} -> {p["count"]} entries (room for {min(room_primary, room_backup)} in both copies)')
    entries[index][3] = ud_last_new
    entries[free[0]] = [IOS_TYPE.bytes_le, uuid.uuid4().bytes_le, ios_first, ios_last, 0,
                        a.name.encode('utf-16-le').ljust(72, b'\0')]
    print(f'LUN 0: {a.blocks} blocks; usable {p["first"]:#x}..{p["last"]:#x}; {p["count"]} entries')
    print(f'userdata  {ud[2]:#x}..{ud[3]:#x} -> {ud[2]:#x}..{ud_last_new:#x}  ({(ud_last_new - ud[2] + 1) * BLOCK / 2**30:.2f} GiB)')
    print(f'{a.name:9} new entry {free[0]}: {ios_first:#x}..{ios_last:#x}  ({(ios_last - ios_first + 1) * BLOCK / 2**30:.2f} GiB), '
          f'start byte {ios_first * BLOCK:#x} ({a.align_kib} KiB aligned: {ios_first % align == 0})')
    write(a, primary, backup, p, b_entries_lba, tail_start, entries, sorted([index, free[0]]))


def write(a, primary, backup, p, b_entries_lba, tail_start, entries, expected):
    array = b''.join(ENTRY.pack(*e) for e in entries)
    if not a.out:
        print('dry run: nothing written')
        return
    new_primary = bytearray(primary)
    new_primary[BLOCK:2 * BLOCK] = header_bytes(p, 1, a.blocks - 1, 2, array)
    new_primary[2 * BLOCK:2 * BLOCK + len(array)] = array
    new_backup = bytearray(backup)
    at = (b_entries_lba - tail_start) * BLOCK
    new_backup[at:at + len(array)] = array
    new_backup[-BLOCK:] = header_bytes(p, a.blocks - 1, 1, b_entries_lba, array)
    # Parse what is about to be written, and compare it with the plan.
    q = parse(bytes(new_primary[BLOCK:2 * BLOCK]), bytes(new_primary[2 * BLOCK:]), 'new primary')
    r = parse(bytes(new_backup[-BLOCK:]), bytes(new_backup[at:]), 'new backup')
    if q['entries'] != entries or r['entries'] != entries or bytes(new_primary[:BLOCK]) != primary[:BLOCK]:
        raise SystemExit('new GPT does not round-trip')
    before = [list(e) for e in p['entries']] + [None] * (len(entries) - len(p['entries']))
    changed = [i for i in range(len(entries)) if entries[i] != before[i]]
    if changed != expected:
        raise SystemExit(f'unexpected entries changed: {changed}')
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / 'new-primary.bin').write_bytes(new_primary)
    (a.out / 'new-backup.bin').write_bytes(new_backup)
    print(f'wrote {a.out / "new-primary.bin"} and {a.out / "new-backup.bin"} (only entries {changed} differ)')


if __name__ == '__main__':
    main()
