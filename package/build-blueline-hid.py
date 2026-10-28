#!/usr/bin/env python3
"""Build the blueline key input for iPadOS 18.7.11 (22H384) as build/blueline-hid/libBluelineHid.a: ShengHidInput's
NativeHidInput (an IOHIDDevice on the original IOHIDFamily-2115) and the board's PON key reader (package/BluelineButtons.cpp).
The blueline counterpart of UniXNU's scripts/build-sheng-hid-input.py (21H461). The 22H384 IOHIDDevice has the 21H461 layout
(207 vtable slots, pure slot 180, the same 18 overrides of IOService's 168, 0xd0 bytes); only its addresses differ. They were
found with ios18-tools/hid18_abi.py (the metaclass constructor call naming "IOHIDDevice", getMetaClass, the vtable holding it,
the constructor storing its vptr) and are checked here against the kernelcache and, shifted by 0x1000000, the built kernel."""
from pathlib import Path
import argparse, hashlib, json, struct, subprocess, sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--tree', type=Path, required=True)
p.add_argument('--components', type=Path, required=True)
p.add_argument('--hid', type=Path, required=True, help='ShengHidInput checkout (5f3a37c: report 2 power/volume bits)')
p.add_argument('--kernel', type=Path, required=True, help='a built kernel of this profile (bindings are checked in it)')
p.add_argument('--kernelcache', type=Path, required=True, help='the decompressed 22H384 kernelcache')
a = p.parse_args()
tree, components, hid = a.tree.resolve(), a.components.resolve(), a.hid.resolve()
sys.path.insert(0, str(components / 'kits/XnuPrelink'))
from native_prelink import macho, backed_offset, exports  # noqa: E402

here = Path(__file__).resolve().parent
out = tree / 'build/blueline-hid'
out.mkdir(parents=True, exist_ok=True)
SHIFT = 0x1000000
SLOTS, SIZE, PURE = 207, 0xd0, [180]
OVERRIDES = [0, 1, 7, 12, 14, 21, 23, 76, 81, 82, 86, 87, 91, 92, 93, 101, 132, 140]
OWNABLE = {0, 1, 7, 14, 86, 87, 111, 171, 173, 180}   # destructors, getMetaClass, free, start, stop, getWorkLoop, strings, descriptor
VTABLE = 0xfffffff006d64688   # __ZTV11IOHIDDevice
C2, D2, META = 0xfffffff005c7ae5c, 0xfffffff005c7ae80, 0xfffffff007be2960
META_SITE = 0xfffffff005c7aea0   # adrp/add x1 "IOHIDDevice"; adrp/ldr x2 super; mov w3, #0xd0


def fail(why):
    raise SystemExit('build-blueline-hid: ' + why)


class Image:
    def __init__(self, path):
        self.raw = path.read_bytes()
        self.im = macho(self.raw)
        self.names = {n: v for v, group in exports(self.raw, self.im).items() for n in group}

    def read(self, va, n):
        at = backed_offset(self.im['segments'], va, n, len(self.raw))
        return self.raw[at:at + n]

    def words(self, va, n):
        return struct.unpack(f'<{n}I', self.read(va, 4 * n))


kc, ours = Image(a.kernelcache), Image(a.kernel)


def sx(v, bits):
    return v - (1 << bits) if v >> (bits - 1) & 1 else v


def adrp(pc, w):
    return (pc & ~0xfff) + (sx(((w >> 29) & 3) | (((w >> 5) & 0x7ffff) << 2), 21) << 12)


def branch(pc, w):
    return pc + sx(w & 0x3ffffff, 26) * 4


def stub_got(stub):
    w = kc.words(stub, 3)
    if (w[0] & 0x9f00001f) != 0x90000010 or (w[1] & 0xffc003ff) != 0xf9400210 or w[2] not in (0xd61f0200, 0xd71f0a1f):
        fail(f'not a GOT stub at {stub:#x}')
    got = adrp(stub, w[0]) + ((w[1] >> 10) & 0xfff) * 8
    return struct.unpack('<Q', kc.read(got, 8))[0]


# 1. The original class, structurally.
w = kc.words(META_SITE, 6)
string = adrp(META_SITE, w[0]) + ((w[1] >> 10) & 0xfff)
if kc.read(string, 12) != b'IOHIDDevice\0' or w[4] != 0x52800003 | (SIZE << 5):
    fail('metaclass constructor call does not name IOHIDDevice with 0xd0 bytes')
entries = struct.unpack(f'<{SLOTS + 1}Q', kc.read(VTABLE + 16, (SLOTS + 1) * 8))
if kc.read(VTABLE, 16) != bytes(16) or entries[SLOTS] != 0 or not all(entries[:SLOTS]):
    fail('vtable is not exactly 207 entries')
g = kc.words(entries[7], 3)
if g[2] != 0xd65f03c0 or adrp(entries[7], g[0]) + ((g[1] >> 10) & 0xfff) != META:
    fail('slot 7 does not return the IOHIDDevice metaclass')
base = struct.unpack('<168Q', kc.read(kc.names['__ZTV9IOService'] + 16, 168 * 8))
if [i for i in range(168) if entries[i] != base[i]] != OVERRIDES:
    fail('IOService overrides differ from 21H461')
pure_kc = kc.names['___cxa_pure_virtual']
if [i for i, v in enumerate(entries) if v == pure_kc] != PURE:
    fail('abstract slot set differs')
c = kc.words(C2, 9)
if (c[0], c[1], c[5], c[7], c[8]) != (0xa9bf7bfd, 0x910003fd, 0x91004108, 0xa8c17bfd, 0xd65f03c0) or c[6] != 0xf9000008 \
        or adrp(C2 + 12, c[3]) + ((c[4] >> 10) & 0xfff) != VTABLE \
        or stub_got(branch(C2 + 8, c[2])) != kc.names['__ZN9IOServiceC2EPK11OSMetaClass']:
    fail('constructor is not IOService(meta) then the IOHIDDevice vptr store')
d = kc.words(D2, 1)[0]
if (d & 0xfc000000) != 0x14000000 or stub_got(branch(D2, d)) != kc.names['__ZN9IOServiceD2Ev']:
    fail('destructor is not a tail call of IOService::~IOService')

# 2. Header for the exact original class (UniXNU scripts/build-sheng-hid-input.py, same layout).
header = '''// Generated by UniXNU-blueline package/build-blueline-hid.py for 22H384: opaque private storage of the original
// IOHIDDevice (IOHIDFamily-2115). Only qualified base prototypes may be called from source.
#pragma once
#include <IOKit/IOService.h>
#include <IOKit/IOMemoryDescriptor.h>
class IOHIDDevice : public IOService {
 OSDeclareAbstractStructors(IOHIDDevice)
 unsigned char originalPrivate[0xd0-sizeof(IOService)];
public:
 using IOService::setProperty;
 IOReturn Dispatch(IORPC) override;
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 bool setProperty(const OSSymbol *,OSObject *) override;
 bool willTerminate(IOService *,IOOptionBits) override;
 IOReturn configureReport(IOReportChannelList *,IOReportConfigureAction,void *,void *) override;
 IOReturn updateReport(IOReportChannelList *,IOReportUpdateAction,void *,void *) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 bool handleOpen(IOService *,IOOptionBits,void *) override;
 void handleClose(IOService *,IOOptionBits) override;
 bool handleIsOpen(const IOService *) const override;
 bool matchPropertyTable(OSDictionary *,SInt32 *) override;
 IOReturn message(UInt32,IOService *,void *) override;
 IOReturn newUserClient(task_t,void *,UInt32,OSDictionary *,IOUserClient **) override;
'''
for i in range(168, SLOTS):
    if i == 171:
        header += ' virtual OSString *newTransportString() const;\n'
    elif i == 173:
        header += ' virtual OSString *newProductString() const;\n'
    elif i == 180:
        header += ' virtual IOReturn newReportDescriptor(IOMemoryDescriptor **) const=0;\n'
    elif i == 181:
        header += ' virtual IOReturn handleReport(IOMemoryDescriptor *,unsigned,IOOptionBits);\n'
    else:
        header += f' virtual __attribute__((deprecated("Unqualified prototype: never call from source"))) void opaque{i}();\n'
header += '};\nstatic_assert(sizeof(IOHIDDevice)==0xd0,"Original device instance size");\n'
(out / 'OriginalHidDeviceABI.h').write_text(header)

# 3. Compile with the XNU C++ recipe. NativeHidInput's class initializer is a local symbol: compile it through a wrapper unit
# that makes it global, keeping the file name so the symbol name is unchanged (as build-blueline-display.py does).
recipe = json.loads((tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
                    .read_text().strip().rstrip(','))


def compile_cpp(src, obj):
    cmd, i, args = [], 0, recipe['arguments']
    while i < len(args):
        v = args[i]
        if v == recipe['file']:
            cmd.append(str(src))
        elif v == '-o':
            cmd += ['-o', str(obj)]
            i += 1
        elif v in ('-MF', '-MJ'):
            i += 1
        elif v == '-Xclang' and args[i + 1].startswith('-W'):
            i += 1
        elif not v.startswith('-W') and v not in ('-MD', '-MP'):
            cmd.append(v)
        i += 1
    cmd += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unnecessary-virtual-specifier', '-Wno-c99-designator',
            '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion', '-Wno-deprecated-declarations',
            '-Wno-overloaded-virtual', '-I', str(out), '-I', str(hid), '-I', str(tree / '.cache/xnu-work/osfmk')]
    run = subprocess.run(cmd, cwd=recipe['directory'], capture_output=True, text=True)
    (out / (obj.stem + '.log')).write_text(run.stdout + run.stderr)
    if run.returncode:
        fail(f'{src}:\n{run.stderr[-5000:]}')
    return obj


wrap_dir = out / 'wrap'
wrap_dir.mkdir(exist_ok=True)
(wrap_dir / 'NativeHidInput.cpp').write_text(
    f'#include "{hid / "NativeHidInput.cpp"}"\n__asm__(".globl \\"__GLOBAL__sub_I_NativeHidInput.cpp\\"");\n')
hid_obj = compile_cpp(wrap_dir / 'NativeHidInput.cpp', out / 'NativeHidInput.o')
buttons_obj = compile_cpp(here / 'BluelineButtons.cpp', out / 'BluelineButtons.o')

# 4. The compiled vtable: exactly 207 entries, owned entries a subset of the qualified ones.
raw = hid_obj.read_bytes()
image = macho(raw)
st = next(q for c_, q, _ in image['commands'] if c_ == 2)
sy, count, strings, _ = struct.unpack_from('<4I', raw, st + 8)
symbols = []
for i in range(count):
    ix, kind, section, _, va = struct.unpack_from('<IBBHQ', raw, sy + i * 16)
    symbols.append((raw[strings + ix:raw.index(b'\0', strings + ix)].decode(), va, kind))
vtable = next(va for name, va, _ in symbols if name == '__ZTV14NativeHidInput')
relocations = {}
for segment in image['segments']:
    for sec in segment['sections']:
        if sec['va'] <= vtable < sec['va'] + sec['size']:
            ro, rc = struct.unpack_from('<2I', raw, sec['command_offset'] + 56)
            for i in range(rc):
                offset, bits = struct.unpack_from('<2I', raw, ro + i * 8)
                if bits >> 27 & 1 and bits >> 28 == 0 and bits >> 25 & 3 == 3:
                    relocations[sec['va'] + offset] = symbols[bits & 0xffffff][0]
compiled = [relocations.get(vtable + 16 + i * 8) for i in range(SLOTS)]
if not all(compiled) or vtable + 16 + SLOTS * 8 in relocations:
    fail('compiled vtable is not exactly 207 entries')
owned = [i for i, n in enumerate(compiled) if n.startswith(('__ZN14NativeHidInput', '__ZNK14NativeHidInput'))]
if not set(owned) <= OWNABLE or 180 not in owned:
    fail(f'owned slots {owned}')

# 5. Bindings: the original entries for every inherited IOHIDDevice method, the constructor, destructor, metaclass and vtable.
bindings = {'__ZN11IOHIDDeviceC2EPK11OSMetaClass': C2, '__ZN11IOHIDDeviceD2Ev': D2, '__ZN11IOHIDDevice10gMetaClassE': META,
            '__ZTV11IOHIDDevice': VTABLE}
for i, name in enumerate(compiled):
    if '11IOHIDDevice' in name:
        if i in PURE:
            fail(f'original abstract slot {i} inherited')
        bindings[name] = entries[i]
    elif i not in owned and ours.names.get(name) != struct.unpack('<Q', ours.read(VTABLE - SHIFT + 16 + 8 * i, 8))[0]:
        fail(f'inherited kernel base symbol at slot {i}: {name}')
undefined = {n for n, _, k in symbols if k == 1 and '11IOHIDDevice' in n}
if undefined - set(bindings):
    fail(f'unbound original imports {sorted(undefined - set(bindings))}')
for name, address in bindings.items():
    if kc.read(address, 16) != ours.read(address - SHIFT, 16):
        fail(f'{name}: kernelcache and built kernel differ at the shifted address')
asm = out / 'original-bindings.S'
asm.write_text(''.join(f'.globl {n}\n.set {n}, {v - SHIFT:#x}\n' for n, v in sorted(bindings.items()) if n in undefined))
sdk = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-c', str(asm), '-o',
                str(out / 'hid-original-bindings.o')], check=True)


# 6. Stage the class initializer out of __mod_init_func: BluelineButtons.cpp runs it after IOHIDFamily is loaded.
def stage_late(obj, initializer):
    data = bytearray(obj.read_bytes())
    ncmds = struct.unpack_from('<I', data, 16)[0]
    at, renamed, external = 32, 0, False
    for _ in range(ncmds):
        cmd_id, size = struct.unpack_from('<2I', data, at)
        if cmd_id == 0x19:
            for s in range(struct.unpack_from('<I', data, at + 64)[0]):
                sect = at + 72 + s * 80
                if data[sect:sect + 16].rstrip(b'\0') == b'__mod_init_func':
                    data[sect:sect + 16] = b'__blueline_init\0'
                    struct.pack_into('<I', data, sect + 64, struct.unpack_from('<I', data, sect + 64)[0] & ~0xff)
                    renamed += 1
        if cmd_id == 2:
            symoff, nsyms, stroff, _ = struct.unpack_from('<4I', data, at + 8)
            for k in range(nsyms):
                strx, ntype = struct.unpack_from('<IB', data, symoff + k * 16)
                if bytes(data[stroff + strx:data.index(b'\0', stroff + strx)]) == initializer.encode():
                    external = bool(ntype & 1)
        at += size
    if renamed != 1 or not external:
        fail(f'{obj.name}: initializer staging (renamed {renamed}, external {external})')
    late = obj.with_suffix('.late.o')
    late.write_bytes(bytes(data))
    return late


hid_late = stage_late(hid_obj, '__GLOBAL__sub_I_NativeHidInput.cpp')
archive = out / 'libBluelineHid.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(hid_late), str(buttons_obj), str(out / 'hid-original-bindings.o')],
               check=True)
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
(out / 'build-report.json').write_text(json.dumps(dict(
    source_sha256={str(s): sha(s) for s in (hid / 'NativeHidInput.cpp', here / 'BluelineButtons.cpp')},
    generated_abi_sha256=sha(out / 'OriginalHidDeviceABI.h'), kernel=str(a.kernel), kernel_sha256=sha(a.kernel),
    archive_sha256=sha(archive), bindings={n: hex(v - SHIFT) for n, v in bindings.items()}, owned_entries=owned,
    hardware_qualified=False), indent=2) + '\n')
print(f'PASS: NativeHidInput for 22H384 ({SLOTS} slots, owned {owned}, {len(bindings)} bindings checked) and the PON keys: {archive}')
