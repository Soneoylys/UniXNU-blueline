#!/usr/bin/env python3
"""Generate the Apple-format device tree (ADT) of the Google Pixel 3 (blueline, SDM845) for the first XNU boot.

The node layout is UniXNU-pocket-max's make-pocket-max-adt.py (itself UniXNU-sheng's make-sheng-adt.py);
the hardware values are the SDM845 ones, each with its source:

  - GICv3 distributor 0x17a00000/0x10000, redistributors 0x17a60000/0x100000 = 8 x 0x20000
    (Linux arch/arm64/boot/dts/qcom/sdm845.dtsi, intc@17a00000; the stock XBL uefiplat.cfg APSS_GIC500_GICD/GICR
    and Mu-Silicium NapaliPkg PcdGicDistributorBase/PcdGicRedistributorsBase agree)
  - eight Kryo 385 cores (4 silver, 4 gold), MPIDR Affinity1 0x000..0x700, PSCI over SMC (sdm845.dtsi /cpus, /psci)
  - architectural timer 19.2 MHz (Qualcomm XO)
  - DRAM base 0x80000000 (sdm845.dtsi memory@80000000). The size is set by the bootloader, so it is an input
    here, measured on the device (the UEFI memory map). There is no default.
  - UFS host controller 0x1d84000 (sdm845.dtsi ufshc@1d84000, stock cmdline androidboot.boot_devices=soc/1d84000.ufshc).
    Its compatible is qcom,sdm845-ufshc, which NativeUfs does not match: the SM8550 UFS code must not program this SoC.

No property is invented beyond these sources and the iPad identity the unchanged iPad7,1 image needs.
"""
from pathlib import Path
import argparse
import os
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]

DRAM_BASE = 0x80000000
ARMIO_BASE = 0x17000000          # APSS block that contains the GIC
ARMIO_SIZE = 0x08000000
GIC_DIST = 0x17a00000
GIC_DIST_SIZE = 0x10000
GIC_REDIST = 0x17a60000
GIC_REDIST_SIZE = 0x100000
GIC_REDIST_STRIDE = 0x20000
TIMEBASE = 19200000
CPU_REGS = [0x000, 0x100, 0x200, 0x300, 0x400, 0x500, 0x600, 0x700]


def node(name, properties=(), children=()):
    properties = [('name', name.encode() + b'\0'), *properties]
    result = struct.pack('<II', len(properties), len(children))
    for key, data in properties:
        if len(key) >= 32:
            raise ValueError(f'ADT property name too long: {key}')
        result += struct.pack('<32sI', key.encode(), len(data))
        result += data + bytes(-len(data) % 4)
    return result + b''.join(children)


def empty_nvram_proxy():
    """A fresh volatile CHRP bank for upstream IODTNVRAM, with no controller (as sheng)."""
    def header(name, size):
        raw = bytearray(struct.pack('<BBH12s', 0x70, 0, size // 16, name))
        checksum = sum(raw)
        while checksum > 255:
            checksum = (checksum & 255) + (checksum >> 8)
        raw[1] = checksum
        return raw

    image = bytearray(8192)
    image[:16] = header(b'nvram', 32)
    image[32:48] = header(b'common', 16 + 2048)
    free_at = 32 + 16 + 2048
    image[free_at:free_at + 16] = header(b'free', len(image) - free_at)
    struct.pack_into('<I', image, 16, zlib.adler32(image[20:]))
    return [('nvram-bank-size', struct.pack('<I', len(image))),
            ('nvram-bank-count', struct.pack('<I', 1)),
            ('nvram-current-bank', struct.pack('<I', 0)),
            ('nvram-proxy-data', bytes(image))]


def dram_size(text):
    try:
        value = int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f'{text!r}: the DRAM size is measured, not assumed; read the RAM ranges of the blueline UEFI memory map '
            'on the device, then set board.toml [payload] dram_size') from None
    if value <= 0 or value % 0x200000 or value > 0x100000000:
        raise argparse.ArgumentTypeError(f'{text}: DRAM size must be a positive multiple of 2 MiB, at most 4 GiB (Pixel 3)')
    return value


def build(args):
    cpu_regs = [int(value, 0) for value in args.cpu_regs.split(',')]
    if (not cpu_regs or cpu_regs[0] != 0 or len(set(cpu_regs)) != len(cpu_regs)
            or any(reg not in CPU_REGS for reg in cpu_regs)):
        raise SystemExit('--cpu-regs: SDM845 cores are 0x000..0x700 in Affinity1, boot CPU 0 first')
    cpus = [node('cpu%d' % index, [('state', b'running\0' if index == 0 else b'waiting\0'),
                                   ('reg', struct.pack('<I', reg)),
                                   ('device_type', b'cpu\0'), ('compatible', b'qcom,kryo385\0'),
                                   ('timebase-frequency', struct.pack('<I', TIMEBASE))])
            for index, reg in enumerate(cpu_regs)]
    ipad = args.ipad_identity
    chosen = [('random-seed', os.urandom(64)),
              *([('unique-chip-id', struct.pack('<Q', args.unique_chip_id)), ('chip-id', struct.pack('<I', args.chip_id))]
                if args.unique_chip_id is not None else []),
              ('osenvironment', b'main\0'),
              *([('certificate-production-status', struct.pack('<I', 0)), ('effective-production-status-ap', struct.pack('<I', 0)),
                 ('certificate-security-mode', struct.pack('<I', 1)), ('effective-security-mode-sep', struct.pack('<I', 1))]
                if ipad else []),
              *([('ephemeral-storage', struct.pack('<I', 1))] if (args.data_ramdisk or args.packed_data_ramdisk) else []),
              *([('darwinboot-memory-size', struct.pack('<Q', args.owned_memory_size))] if args.owned_memory_size else []),
              ('dram-base', struct.pack('<Q', DRAM_BASE)),
              ('dram-size', struct.pack('<Q', args.dram_size)),
              ('crypto-hash-method', b'sha2-384\0'),
              *([('root-matching', ('<dict><key>IOProviderClass</key><string>IOMedia</string>'
                                    '<key>IOPropertyMatch</key><dict><key>UUID</key><string>' +
                                    args.root_match_volume_uuid.upper() + '</string></dict></dict>\0').encode())]
                if args.root_match_volume_uuid else []),
              ('amfi-only-platform-code', struct.pack('<I', 1)),
              *([('text-fixups', b''.join(f.encode() + b'\0' for f in args.text_fixup))] if args.text_fixup else []),
              *empty_nvram_proxy()]
    memory_map = ([('PackedRAMDisk' if args.packed_data_ramdisk else 'RAMDisk', bytes(16))]
                  if (args.data_ramdisk or args.packed_data_ramdisk) else []) + \
                 ([('TrustCache', bytes(16))] if args.trust_cache else [])
    return node('device-tree', [
        ('model', b'iPad7,1\0' if ipad else b'blueline-first-boot\0'),
        *([('target-type', b'J120\0')] if ipad else []),
        # Root compatible strings of the board DT (Linux sdm845-google-blueline.dts).
        ('compatible', b'google,blueline\0qcom,sdm845\0'),
        ('#address-cells', struct.pack('<I', 2)),
        ('#size-cells', struct.pack('<I', 2)),
    ], [
        node('chosen', chosen, [node('memory-map', memory_map), node('manifest-properties')]),
        node('cpus', children=cpus),
        *([node('psci', [('compatible', b'arm,psci-1.0\0'), ('method', b'smc\0')])] if len(cpus) > 1 else []),
        node('defaults', [('sleep-disabled', struct.pack('<I', 1))]),
        node('options', []),
        # Power, volume up and volume down (PM8998 PON power key and RESIN, volume-up GPIO key); no Home button.
        *([node('buttons', [('compatible', b'buttons\0'), ('device_type', b'buttons\0'),
                            ('button-names', b'hold\0voldown\0volup\0'),
                            ('home-button-type', struct.pack('<I', 2))])] if ipad else []),
        node('product', [('product-name', b'iPad Pro (12.9-inch) (2nd generation)\0' if ipad else b'Google Pixel 3\0'),
                         ('model', b'google,blueline\0'),
                         *([('product-description', b'iPad Pro (12.9-inch) (2nd generation)\0'),
                            ('artwork-device-idiom', b'pad\0'),
                            ('artwork-device-subtype', struct.pack('<I', 2732)),
                            ('artwork-scale-factor', struct.pack('<I', 2)),
                            ('artwork-display-gamut', b'P3\0'),
                            ('compatible-device-fallback', b'iPad6,7\0'),
                            ('thin-bezel', struct.pack('<I', 1))] if ipad else [])]),
        node('arm-io', [('device_type', b'arm-io\0'),
                        ('#address-cells', struct.pack('<I', 2)),
                        ('#size-cells', struct.pack('<I', 2)),
                        ('ranges', struct.pack('<QQQ', 0, ARMIO_BASE, ARMIO_SIZE))],
             [node('intc', [('compatible', b'arm,gic-v3\0'),
                            ('reg', struct.pack('<4Q', GIC_DIST - ARMIO_BASE, GIC_DIST_SIZE,
                                                GIC_REDIST - ARMIO_BASE, GIC_REDIST_SIZE)),
                            ('redistributor-stride', struct.pack('<Q', GIC_REDIST_STRIDE))]),
              # The UFS host (sdm845.dtsi ufshc@1d84000) lies below the arm-io window and serves matching only.
              node('ufshc', [('compatible', b'qcom,sdm845-ufshc\0')])]),
    ])


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--output', type=Path, default=ROOT / 'build/blueline/kernel.macho.adt')
    p.add_argument('--dram-size', type=dram_size, required=True, help='measured DRAM size from 0x80000000')
    p.add_argument('--owned-memory-size', type=lambda v: int(v, 0), default=None,
                   help='DarwinBoot contiguous window request, 256 MiB..4 GiB in 2 MiB steps')
    p.add_argument('--root-match-volume-uuid', default=None)
    p.add_argument('--unique-chip-id', type=lambda v: int(v, 16), default=None)
    p.add_argument('--chip-id', type=lambda v: int(v, 16), default=None)
    p.add_argument('--ipad-identity', action='store_true')
    p.add_argument('--cpu-regs', default='0', help='comma-separated MPIDR Affinity1 values, boot CPU first (default: one CPU)')
    p.add_argument('--psci-method', choices=['smc'], default='smc', help='SDM845 PSCI is SMC only')
    p.add_argument('--packed-data-ramdisk', action='store_true')
    p.add_argument('--data-ramdisk', action='store_true')
    p.add_argument('--trust-cache', action='store_true')
    p.add_argument('--text-fixup', action='append', default=[],
                   help='/chosen/text-fixups entry <file name>,<file offset>,<original>,<replacement> for the kernel signed-text fixups (as sheng)')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if (args.unique_chip_id is None) != (args.chip_id is None):
        p.error('--unique-chip-id and --chip-id must be supplied together')
    if args.owned_memory_size is not None and (not 0x10000000 <= args.owned_memory_size <= 0x100000000
                                               or args.owned_memory_size % 0x200000):
        p.error('owned memory must be 256 MiB to 4 GiB, aligned to 2 MiB')
    if args.owned_memory_size is not None and args.owned_memory_size > args.dram_size:
        p.error('owned memory is larger than the DRAM size')
    tree = build(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(tree)
    print(f'blueline ADT: {len(tree)} bytes, dram {args.dram_size:#x}, {args.cpu_regs.count(",") + 1} CPU(s)')


if __name__ == '__main__':
    main()
