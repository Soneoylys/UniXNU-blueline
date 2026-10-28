#!/usr/bin/env python3
"""Build the staged AGXAccelerator class (Adreno740 NativeAdrenoAccelerator.cpp) for 22H384, as UniXNU's
scripts/build-adreno740-accelerator.py does for 21H461, without Homebrew LLVM:
  - the class's static initializer is made global with an assembler directive in a wrapper unit, and its
    __mod_init_func section is renamed to a plain section by editing the object's section header, so the
    initializer runs only when BluelineGpu.cpp calls it after IOGPUFamily has loaded;
  - the three original IOAcceleratorES imports are bound to their addresses in our prelinked kernel.
The bindings were derived from the 22H384 kernelcache (IOGPUFamily __TEXT_EXEC 0xfffffff005f20000) and checked byte for
byte in kernel-k20.macho, where the prelinker places IOGPUFamily 0x1000000 lower (as it did on 21H461):
  IOAcceleratorES::IOAcceleratorES(OSMetaClass const*)  kernelcache 0xfffffff005f40704  ours 0xfffffff004f40704
  IOAcceleratorES::~IOAcceleratorES()                    kernelcache 0xfffffff005f40750  ours 0xfffffff004f40750
  IOAcceleratorES::gMetaClass                            kernelcache 0xfffffff007beced8  ours 0xfffffff006beced8
--kernel re-checks them against a built kernel before writing the archive."""
from pathlib import Path
import argparse, hashlib, json, struct, subprocess, sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--tree', type=Path, required=True)
p.add_argument('--components', type=Path, required=True)
p.add_argument('--kernel', type=Path, required=True, help='a built kernel of this profile, to check the bindings')
a = p.parse_args()
tree, components = a.tree.resolve(), a.components.resolve()
sys.path.insert(0, str(components / 'kits/XnuPrelink'))
from native_prelink import macho, backed_offset  # noqa: E402

out = tree / 'build/blueline-accelerator'
(out / 'inc/NativeMetal').mkdir(parents=True, exist_ok=True)
(out / 'inc/x').mkdir(exist_ok=True)
(out / 'inc/NativeMetal/OriginalAcceleratorABI.h').write_bytes(
    (components / 'drivers/AdrenoMetal/OriginalAcceleratorABI.h').read_bytes())
source = components / 'drivers/Adreno740/NativeAdrenoAccelerator.cpp'
unit = out / 'NativeAdrenoAccelerator.cpp'   # same file name, so the initializer keeps its name
unit.write_text('#include "%s"\n__asm__(".globl \\"__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp\\"");\n'
                % str(source))

BINDINGS = {'__ZN15IOAcceleratorESC2EPK11OSMetaClass': 0xfffffff004f40704,
            '__ZN15IOAcceleratorESD2Ev': 0xfffffff004f40750,
            '__ZN15IOAcceleratorES10gMetaClassE': 0xfffffff006beced8}
# Expected code at the bindings (kernelcache bytes, little-endian words): C2 = stp/mov/bl/adrp/add/add/str/ldp/ret
# with the IOAcceleratorES vtable 0xfffffff006e0fa68 (kernelcache) + 16; D2 = b IOService::~IOService.
raw = a.kernel.read_bytes()
im = macho(raw)


def read(va, n):
    at = backed_offset(im['segments'], va, n, len(raw))
    return raw[at:at + n]


c2 = struct.unpack('<9I', read(BINDINGS['__ZN15IOAcceleratorESC2EPK11OSMetaClass'], 36))
if c2[0] != 0xa9bf7bfd or c2[1] != 0x910003fd or c2[6] != 0xf9000008 or c2[8] != 0xd65f03c0:
    sys.exit('IOAcceleratorES constructor not at the bound address in ' + str(a.kernel))
if struct.unpack('<I', read(BINDINGS['__ZN15IOAcceleratorESD2Ev'], 4))[0] >> 26 != 0x05:
    sys.exit('IOAcceleratorES destructor branch not at the bound address')
getmeta = struct.unpack('<3I', read(0xfffffff004f40798, 12))   # IOAcceleratorES::getMetaClass: adrp/add x0; ret
page = 0xfffffff004f40798 & ~0xfff
imm = ((getmeta[0] >> 5) & 0x7ffff) << 2 | ((getmeta[0] >> 29) & 3)
imm = imm - (1 << 21) if imm & (1 << 20) else imm
if page + (imm << 12) + ((getmeta[1] >> 10) & 0xfff) != BINDINGS['__ZN15IOAcceleratorES10gMetaClassE']:
    sys.exit('IOAcceleratorES metaclass differs from the bound address')

recipe = json.loads((tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
                    .read_text().strip().rstrip(','))
obj = out / 'NativeAdrenoAccelerator.o'
cmd, i, args = [], 0, recipe['arguments']
while i < len(args):
    v = args[i]
    if v == recipe['file']:
        cmd.append(str(unit))
    elif v == '-o':
        cmd += ['-o', str(obj)]
        i += 1
    elif v == '-Xclang' and args[i + 1].startswith('-W'):
        i += 1
    elif not v.startswith('-W') and v not in ('-MD', '-MP'):
        cmd.append(v)
    i += 1
    if v in ('-MF', '-MJ'):
        i += 1
cmd += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unnecessary-virtual-specifier', '-Wno-c99-designator',
        '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion', '-Wno-overloaded-virtual',
        '-I', str(out / 'inc/x'), '-I', str(tree / '.cache/xnu-work/osfmk')]
subprocess.run(cmd, cwd=recipe['directory'], check=True)

# Stage the initializer: __DATA,__mod_init_func (S_MOD_INIT_FUNC_POINTERS) -> __DATA,__blueline_init (S_REGULAR).
data = bytearray(obj.read_bytes())
ncmds = struct.unpack_from('<I', data, 16)[0]
at, renamed, initializer = 32, 0, False
for _ in range(ncmds):
    cmd_id, size = struct.unpack_from('<2I', data, at)
    if cmd_id == 0x19:   # LC_SEGMENT_64
        nsects = struct.unpack_from('<I', data, at + 64)[0]
        for s in range(nsects):
            sect = at + 72 + s * 80
            if data[sect:sect + 16].rstrip(b'\0') == b'__mod_init_func':
                data[sect:sect + 16] = b'__blueline_init\0'
                flags = struct.unpack_from('<I', data, sect + 64)[0]
                struct.pack_into('<I', data, sect + 64, flags & ~0xff)
                renamed += 1
    if cmd_id == 2:      # LC_SYMTAB: the initializer must be an external definition now
        symoff, nsyms, stroff, _ = struct.unpack_from('<4I', data, at + 8)
        for k in range(nsyms):
            strx, ntype = struct.unpack_from('<IB', data, symoff + k * 16)
            name = bytes(data[stroff + strx:data.index(b'\0', stroff + strx)])
            if name == b'__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp':
                initializer = bool(ntype & 1) and (ntype & 0x0e) == 0x0e
    at += size
if renamed != 1 or not initializer:
    sys.exit(f'initializer staging failed (sections renamed {renamed}, external initializer {initializer})')
late = out / 'NativeAdrenoAccelerator.late.o'
late.write_bytes(bytes(data))
undefined = {l.split()[-1] for l in subprocess.run(['xcrun', 'nm', '-u', str(late)], capture_output=True, text=True).stdout.split('\n')
             if 'IOAcceleratorES' in l}
if not undefined or undefined - set(BINDINGS):
    sys.exit('Unexpected original-base imports: %s' % sorted(undefined - set(BINDINGS)))
asm = out / 'original-bindings.S'
asm.write_text(''.join(f'.globl {n}\n.set {n}, {v:#x}\n' for n, v in sorted(BINDINGS.items()) if n in undefined))
sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-c', str(asm), '-o',
                str(out / 'original-bindings.o')], check=True)
archive = out / 'libBluelineAccelerator.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(late), str(out / 'original-bindings.o')], check=True)
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
(out / 'build-report.json').write_text(json.dumps(dict(
    source_sha256=sha(source), kernel_checked=str(a.kernel), kernel_sha256=sha(a.kernel), archive_sha256=sha(archive),
    bindings={n: hex(v) for n, v in BINDINGS.items()}, runtime_qualified=False), indent=2) + '\n')
print('PASS: staged AGXAccelerator for 22H384 with the IOAcceleratorES bindings checked in', a.kernel.name)
