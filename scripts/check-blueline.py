#!/usr/bin/env python3
"""Board preflight: refuse a blueline board file or firmware input set with copied or unmeasured values.

    check-blueline.py [board.toml]

The board follows sheng and the Pocket Max (sheng's package is kits/sheng, unchanged), so the likely mistakes are
copied hardware values: sheng's SM8550 or the Pocket Max's SM8250 addresses, their kernel arguments for that
hardware, a DRAM size or userdata range that was not measured, or firmware inputs that are not the pinned Pixel 3 files.
"""
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOREIGN_VALUES = ('0x17100000', '0x17180000', 'sm8550', 'sm8250', '0x2f3840', '0x3d0abfa', '0xb8000000', '0xa7100000',
                  '0x9c000000')
# sheng and Pocket Max arguments that drive their SoC's hardware (NativeUfs, Adreno, touch, cpufreq, SD card).
FOREIGN_ARGUMENTS = ('native-ufs-root', 'sheng-touch', 'sheng-gpu-power', 'sheng-gpu-metal', 'sheng-gpu-perf', 'sheng-gpu-addon',
                     'sheng-fb-gpu', 'sheng-data-persist', 'sheng-banner', 'armcpu-spi-ipi', 'sheng-cpufreq', 'pm-touch',
                     'pm-landscape', 'pm-sd-dma', 'pm-gpu', 'pm-buttons', 'sheng-smp')
SDM845_CPUS = {0x000, 0x100, 0x200, 0x300, 0x400, 0x500, 0x600, 0x700}
FIRMWARE = ROOT / '.cache' / 'firmware'
PINNED = {
    'bootloader-blueline-b1c1-0.4-7617406.img': '85a788ecb531b4d7351e7c867159c29bd40d1ad849425040e18c59cfb90d5dc6',
    'boot.img': '5c1ce19e814e2f198baa877a7351306079272fe67e13404e31f3bab213bb506e',
}


def problems(board, text):
    found = []
    if board.get('board', {}).get('soc') != 'SDM845':
        found.append('[board] soc must be SDM845')
    for value in FOREIGN_VALUES:
        if value in text.lower():
            found.append(f'board file mentions the sheng/Pocket Max value {value}')
    fw = board.get('firmware', {})
    if fw.get('config') != 'blueline' or 'Google/bluelinePkg' not in fw.get('dsc', ''):
        found.append('[firmware] must build Platforms/Google/bluelinePkg with Resources/Configs/blueline.toml')
    image = board.get('bootimg', {}).get('image', {})
    if (image.get('kernel_offset'), image.get('tags_offset'), image.get('pagesize')) != (0x8000, 0x100, 4096):
        found.append('[bootimg.image] must keep the stock Pixel 3 kernel 0x8000, tags 0x100, 4 KiB pages')
    if 'userdata' in board and board['userdata'].get('lba_bytes') != 4096:
        found.append('[userdata] LBAs are 4096-byte UFS blocks')
    payload = board.get('payload', {})
    dram = str(payload.get('dram_size', ''))
    if not dram.startswith('TODO'):
        try:
            value = int(dram, 0)
            if value % 0x200000 or not 0x80000000 <= value <= 0x100000000:
                found.append(f'[payload] dram_size {dram} is not a 2 MiB multiple between 2 and 4 GiB')
        except ValueError:
            found.append(f'[payload] dram_size {dram!r} is neither TODO(device) nor a number')
    for argument in payload.get('kernel_args', []):
        if argument.split('=', 1)[0] in FOREIGN_ARGUMENTS:
            found.append(f'kernel argument {argument} drives sheng or Pocket Max hardware')
    adt = payload.get('adt', {})
    try:
        regs = [int(v, 0) for v in str(adt.get('cpu_regs', '0')).split(',')]
        if regs[0] != 0 or not set(regs) <= SDM845_CPUS:
            found.append(f'[payload.adt] cpu_regs {adt.get("cpu_regs")} are not SDM845 cores 0x000..0x700, boot CPU first')
    except ValueError:
        found.append('[payload.adt] cpu_regs are not integers')
    owned = int(str(adt.get('owned_memory_size', '0')), 0)
    if owned > 0xC0000000:
        found.append(f'[payload.adt] owned_memory_size {owned:#x} leaves too little of the 4 GB for the firmware and carve-outs')
    if adt.get('tool') != 'scripts/make-blueline-adt.py':
        found.append('[payload.adt] tool must be scripts/make-blueline-adt.py')
    for archive in board.get('kernel', {}).get('archives', []):
        if re.search(r'NativeUfs|NativeAdreno|NativeSdhci|sheng', archive):
            found.append(f'kernel archive "{archive}" programs sheng or Pocket Max hardware')
    return found


def inputs():
    found = []
    for name, digest in PINNED.items():
        path = FIRMWARE / name
        if not path.is_file():
            found.append(f'{path} missing: run scripts/fetch-blueline-factory.py')
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            found.append(f'{path} is not the pinned file')
    manifest = FIRMWARE / 'xbl-extract' / 'manifest.json'
    if not manifest.is_file():
        found.append(f'{manifest} missing: run scripts/extract-blueline-xbl.py')
    else:
        data = json.loads(manifest.read_text())
        xbl = FIRMWARE / 'bootloader' / 'xbl.img'
        if not xbl.is_file() or hashlib.sha256(xbl.read_bytes()).hexdigest() != data['source_sha256']:
            found.append('xbl-extract does not come from bootloader/xbl.img')
    return found


def main(argv=None):
    path = Path(argv[0]) if argv else ROOT / 'board.toml'
    text = path.read_text(encoding='utf-8')
    # Comments may name sheng or the Pocket Max for provenance; only values count.
    values = '\n'.join(line.split('#', 1)[0] for line in text.splitlines())
    found = problems(tomllib.loads(text), values) + inputs()
    for item in found:
        print(f'{path.name}: {item}')
    if not found:
        print(f'{path.name}: blueline preflight passed')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
