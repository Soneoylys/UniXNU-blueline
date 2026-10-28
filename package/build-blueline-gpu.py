#!/usr/bin/env python3
"""Build the blueline GPU archive: the board bring-up (BluelineGpu.cpp), the UFS diagnostic log (BluelineLog.c), the board
OPP policy, the native Adreno publication, and the pinned A630 firmware, with the resolved tree's XNU C/C++ recipes. Links against
libSdm845GpuCore.a (UniXNU-sdm845-common kits/Sdm845Gpu/build-native.py), which must be built first."""
from pathlib import Path
import argparse, hashlib, importlib.util, json, subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--tree', type=Path, required=True)
p.add_argument('--components', type=Path, required=True)
p.add_argument('--soc', type=Path, required=True)
p.add_argument('--firmware', type=Path, required=True, help='directory holding the pinned files of firmware/adreno630.json')
a = p.parse_args()
tree, components, soc = a.tree.resolve(), a.components.resolve(), a.soc.resolve()
here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sdm845_gpu_build', soc / 'kits/Sdm845Gpu/build-native.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
out = tree / 'build/blueline-gpu'
out.mkdir(parents=True, exist_ok=True)
obj_dir = tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
recipes = {'.c': json.loads((obj_dir / 'bsd/DEVELOPMENT/bsd_init.o.json').read_text().strip().rstrip(',')),
           '.cpp': json.loads((obj_dir / 'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))}
core = components / 'drivers/Adreno740'
sources = [here / 'BluelineGpuPolicy.c', here / 'BluelineGpu.cpp', here / 'BluelineLog.c', core / 'NativeAdrenoSha256.cpp',
           core / 'NativeAdrenoPublish.cpp']
includes = [here, core, components / 'kits/ArmSmmu4K', components / 'kits/AdrenoFirmware', components / 'kits/AdrenoPM4',
            soc / 'kits/Sdm845Gpu', tree / '.cache/xnu-work/osfmk']
objects, records = [], []
for source in sources:
    obj = out / (source.stem + '.o')
    recipe = recipes[source.suffix]
    cmd = module.command(recipe, source, obj, includes)
    run = subprocess.run(cmd, cwd=recipe['directory'], capture_output=True, text=True)
    (out / (source.stem + '.log')).write_text(run.stdout + run.stderr, encoding='utf-8')
    if run.returncode:
        raise SystemExit(f'{source}:\n{run.stderr[-6000:]}')
    records.append(dict(source=str(source), source_sha256=module.sha(source), object=module.audit_object(obj)))
    objects.append(obj)
# Firmware as strong definitions of the symbols NativeAdreno uses (sheng build-adreno740-kits.py does the same).
pins = {f['name']: f for f in json.loads((here.parent / 'firmware/adreno630.json').read_text())['files']}
embedded = [('_native_adreno_firmware_image', 'a630_gmu.bin'), ('_native_adreno_sqe_firmware_image', 'a630_sqe.fw'),
            ('_native_adreno_zap_image', 'a630_zap.elf')]
assembly = ['.section __DATA_CONST,__const']
for label, name in embedded:
    path = a.firmware / name
    blob = path.read_bytes() if path.is_file() else b''
    if len(blob) != pins[name]['bytes'] or hashlib.sha256(blob).hexdigest() != pins[name]['sha256']:
        raise SystemExit(f'firmware {name} is missing or does not match its pin')
    assembly += ['.p2align 12', '.globl ' + label, label + ':', '.incbin ' + json.dumps(str(path)), label + '_end:',
                 '.p2align 3', '.globl ' + label + '_size', label + '_size:', '.quad ' + label + '_end-' + label]
(out / 'firmware.S').write_text('\n'.join(assembly) + '\n')
subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-Wno-incompatible-sysroot', '-c', out / 'firmware.S',
                '-o', out / 'firmware.o'], check=True)
objects.append(out / 'firmware.o')
archive = out / 'libBluelineGpu.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), *map(str, objects)], check=True)
report = dict(objects=records, archive=str(archive), archive_sha256=module.sha(archive), hardware_qualified=False)
(out / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(f'PASS: {len(objects)} blueline GPU objects (ARM64/iOS/-Werror) in {archive}')
