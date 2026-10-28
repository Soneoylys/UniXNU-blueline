#!/usr/bin/env python3
"""Decode the blueline UFS diagnostic log (package/BluelineLog.c, sheng-log=1) from a raw dump of its window.

The window is the last 32 MiB of the darwin partition: 8192 blocks of 4096 bytes from absolute LBA 0x1dc1700 on LUN 0
(board.toml [userdata] last_lba 0x1dc36ff, NativeUfs kLogReserveBlocks). scripts/blueline-dump.ps1 pulls it from TWRP as
blueline-log.bin. The format is sheng's (header "SHLOG001"), so this is read-sheng-log.py's --decode without its serial
transport, and it runs on Windows.

usage: blueline-log.py blueline-log.bin [--out DIR]
Output: DIR/<tag>-<boot id>/ header.txt, msgbuf.txt (the kernel message buffer at the last round), slots.txt (every text
slot of the run, oldest first) and latest.txt."""
import argparse
import struct
from pathlib import Path

BLOCK = 4096
HEADER = struct.Struct('<8sIIQQ8I6I32s')
FIELDS = ('magic', 'version', 'header_bytes', 'boot_id', 'first_lba', 'window_blocks', 'msg_first', 'msg_blocks', 'msg_bytes',
          'slot_first', 'slot_blocks', 'slot_count', 'slots_written', 'uptime_s', 'rounds', 'write_errors', 'gpt_open', 'gpt_last',
          'state', 'tag')
STATES = {0: 'off', 1: 'waiting for the window', 2: 'writing', 3: 'stopped after errors', 4: 'stopped (GPT changed)', 5: 'panic'}


def decode(blob, out):
    if len(blob) < BLOCK:
        raise SystemExit(f'log window too short ({len(blob)} bytes)')
    h = dict(zip(FIELDS, HEADER.unpack_from(blob)))
    if h['magic'] != b'SHLOG001':
        raise SystemExit(f'no log header (magic {h["magic"]!r}): the writer never ran, or this is not the log window')
    tag = h['tag'].split(b'\0')[0].decode(errors='replace') or 'untagged'
    run = out / f'{tag}-{h["boot_id"]:x}'
    run.mkdir(parents=True, exist_ok=True)
    def text(v):
        return v.split(b'\0')[0].decode(errors='replace') if isinstance(v, bytes) else v
    lines = [f'{k}={text(v)}' for k, v in h.items()]
    (run / 'header.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    msg = blob[h['msg_first'] * BLOCK:(h['msg_first'] + h['msg_blocks']) * BLOCK][:h['msg_bytes']]
    (run / 'msgbuf.txt').write_bytes(msg)
    count, written = h['slot_count'], h['slots_written']
    slots = []
    for n in range(max(0, written - count), written):
        at = (h['slot_first'] + (n % count) * h['slot_blocks']) * BLOCK
        slots.append(blob[at:at + h['slot_blocks'] * BLOCK].split(b'\0')[0].decode(errors='replace'))
    (run / 'slots.txt').write_text('\n'.join(slots), encoding='utf-8')
    (run / 'latest.txt').write_text(slots[-1] if slots else '', encoding='utf-8')
    print(f'{run}: uptime {h["uptime_s"]} s, {written} slots (kept {len(slots)}), {h["rounds"]} rounds, '
          f'{h["write_errors"]} write errors, state {STATES.get(h["state"], h["state"])}, msgbuf {len(msg)} bytes')
    return run


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('window', type=Path)
    ap.add_argument('--out', type=Path, default=Path('build/blueline-logs'))
    a = ap.parse_args()
    run = decode(a.window.read_bytes(), a.out)
    print((run / 'latest.txt').read_text(encoding='utf-8'))


if __name__ == '__main__':
    main()
