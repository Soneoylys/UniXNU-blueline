#!/usr/bin/env python3
"""Board preflight: refuse a blueline board file or firmware input set with copied or unmeasured values.

    check-blueline.py [board.toml]

The board started from sheng and the Pocket Max, so the likely mistakes are copied hardware values: sheng's SM8550
or the Pocket Max's SM8250 addresses, their kernel arguments for that hardware, a DRAM size or userdata range that was
not measured, or firmware inputs that are not the pinned Pixel 3 files. The CPU, GIC and timer values live in
dts/blueline.dts and the sdm845 SoC repository (format 2), which the common ADT generator reads.
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
# sheng and Pocket Max arguments that drive their SoC's hardware (Adreno, touch, cpufreq, SD card). native-ufs-root is
# allowed: blueline's NativeUfs is the SDM845 copy in UniXNU-sdm845-common, run with the SDM845 SMMU arguments.
# sheng-smp and sheng-data-persist are generic ArmCpu/RamBlock gates backed by this SDM845 copy.
FOREIGN_ARGUMENTS = ('sheng-touch', 'sheng-gpu-power', 'sheng-gpu-metal', 'sheng-gpu-perf', 'sheng-gpu-addon',
                     'sheng-fb-gpu', 'sheng-banner', 'armcpu-spi-ipi', 'sheng-cpufreq', 'pm-touch',
                     'pm-landscape', 'pm-sd-dma', 'pm-gpu', 'pm-buttons')
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
    dram = str(payload.get('dram_size', 'TODO'))
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
    if 'sheng-data-persist=2' in payload.get('kernel_args', []):
        components = board.get('components', {}).get('include', [])
        if 'RamBlock' not in components or 'ShengUfs' not in components or not adt.get('persistent_data'):
            found.append('persistent Data requires RamBlock, SDM845 ShengUfs and persistent_data=true')
        if 'native-ufs-partition=darwin' not in payload.get('kernel_args', []):
            found.append('persistent Data must use the measured darwin partition')
    owned = int(str(adt.get('owned_memory_size', '0')), 0)
    if owned > 0xC0000000:
        found.append(f'[payload.adt] owned_memory_size {owned:#x} leaves too little of the 4 GB for the firmware and carve-outs')
    if board.get('schema_version') != 2 or board.get('board', {}).get('soc_source') != 'sdm845':
        found.append('board.toml must be format 2 (schema_version = 2) with soc_source = "sdm845"')
    if 'armcpu-timer-intid=19' not in payload.get('kernel_args', []):
        found.append('kernel_args must pass armcpu-timer-intid=19 (SDM845 virtual timer, PPI 3)')
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
