#!/usr/bin/env python3
"""Prepare a packed APFS Data seed from the unchanged 22H384 OS on the build Mac.

Adapted from UniXNU prepare-owner-data-fixture.py and prepare-offline-data.py.
The UFS persistent overlay stores changes inside darwin; this does not flash or
replace its read-only OS/SystemOS image. Keep the seed and store geometry stable
between kernel builds. No keys or user records are manufactured.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys


def run(*arguments):
    return subprocess.check_output(arguments)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unixnu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gib', type=int, default=80)
    args = parser.parse_args()
    root, output = args.unixnu.resolve(), args.output.resolve()
    if not 3 <= args.gib <= 80:
        parser.error('This measured darwin partition permits a Data seed of 3..80 GiB')
    if output.exists():
        parser.error('Refuse to overwrite an existing Data seed directory')
    output.mkdir(parents=True)
    source_mount, target = output / 'os-mount', output / 'data-mount'
    source_mount.mkdir(); target.mkdir()
    source_image = root / 'out/ipsw/22H384/094-32804-044.dmg'
    attached = plistlib.loads(run('hdiutil', 'attach', '-readonly', '-nobrowse', '-owners', 'off',
                                 '-mountpoint', str(source_mount), '-plist', str(source_image)))
    source_device = next(e['dev-entry'] for e in attached['system-entities'] if 'dev-entry' in e)
    raw = output / 'data-base.cdr'
    count = total = 0
    try:
        source_info = plistlib.loads(run('diskutil', 'info', '-plist', str(source_mount)))
        launchd_hash = hashlib.sha256((source_mount / 'sbin/launchd').read_bytes()).hexdigest()
        if source_info['VolumeUUID'] != '1E171ABC-554E-4A69-B7A7-35169FAC3565' or launchd_hash != \
                '856540621959abb7db19ca6125381a371ba6286acbb1435173beb99916786f17':
            raise RuntimeError('Require the unchanged original 22H384 OS volume')
        subprocess.run(['hdiutil', 'create', '-size', '64m', '-layout', 'NONE', '-fs', 'APFS',
                        '-volname', 'BluelineData', '-type', 'UDTO', str(raw)], check=True)
        data_info = plistlib.loads(run('hdiutil', 'attach', '-nobrowse', '-owners', 'off',
                                      '-mountpoint', str(target), '-plist', str(raw)))
        data_device = next(e['dev-entry'] for e in data_info['system-entities'] if 'dev-entry' in e)
        try:
            volume = plistlib.loads(run('diskutil', 'info', '-plist', str(target)))
            source = source_mount / 'private/var'
            for current, dirs, files in os.walk(source, followlinks=False):
                dirs[:] = [name for name in dirs if name != 'staged_system_apps']
                destination = target / Path(current).relative_to(source)
                destination.mkdir(exist_ok=True)
                os.chmod(destination, stat.S_IMODE(Path(current).stat().st_mode))
                for name in list(dirs) + files:
                    src, dst = Path(current) / name, destination / name
                    if src.is_symlink():
                        os.symlink(os.readlink(src), dst)
                        if name in dirs: dirs.remove(name)
                    elif src.is_file():
                        shutil.copy2(src, dst)
                        assert hashlib.sha256(src.read_bytes()).digest() == hashlib.sha256(dst.read_bytes()).digest()
                        count += 1; total += src.stat().st_size
            directories = ('db/dslocal/nodes/Default/users', 'db/dslocal/nodes/Default/groups',
                'db/dslocal/nodes/Default/config', 'containers/Data/System', 'containers/Data/Application',
                'containers/Shared/SystemGroup', 'containers/Bundle', 'containers/Temp', 'PersonaVolumes',
                'preboot', 'root', 'tmp', 'run', 'Library', 'keybags/backup', 'mobile/Library/Preferences',
                'mobile/Library/Caches', 'mobile/Library/Logs', 'mobile/Containers', 'mobile/Documents',
                'mobile/Media', 'mobile/tmp')
            for name in directories: (target / name).mkdir(parents=True, exist_ok=True)
            os.chmod(target / 'tmp', 0o1777)
        finally:
            subprocess.run(['hdiutil', 'detach', data_device], check=True)
    finally:
        subprocess.run(['hdiutil', 'detach', source_device], check=True)
    sys.path.insert(0, str(root / 'drivers/DarwinBoot/Test'))
    import packed_ramdisk
    base = output / 'data-base.dbr'
    packed_ramdisk.pack(raw, base)
    seed = output / 'blueline-data.dbr'
    subprocess.run([sys.executable, str(root / 'scripts/seed-data-grow.py'), '--gib', str(args.gib),
                    str(base), str(seed)], check=True)
    identity, header = packed_ramdisk.inspect(seed)
    assert header[32:36] == b'NXSB' and identity['bytes'] == args.gib * 1024**3
    # NativeUfs reserves 1 GiB at the end and a 4 MiB guard after the existing root image.
    first, last = 0x5f9000, 0x1dc36ff
    pages = identity['bytes'] // 4096
    store_blocks = 1 + (pages + 32767) // 32768 + pages
    store_first = last + 1 - 1024 * 256 - store_blocks
    assert store_first >= first + (10809769984 + 4095) // 4096 + 1024
    report = dict(volume_uuid=volume['VolumeUUID'], source_launchd_sha256=launchd_hash,
        source_regular_files=count, source_regular_bytes=total, **identity,
        seed_file=seed.name, store_first_lba=hex(store_first), store_blocks=store_blocks,
        store_last_lba=hex(store_first + store_blocks - 1), reserve_mib=1024,
        os_inputs_modified=False, original_files_preserved=True, added_keys_or_user_records=False,
        persistent_hardware_verified=False)
    (output / 'seed.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
