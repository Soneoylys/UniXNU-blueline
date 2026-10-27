#!/usr/bin/env python3
"""Write a UniXNU payload volume (the boot image ramdisk) without mtools, for the Windows host.

    python scripts/build-blueline-payload.py -o build/payload/blueline-menu.img [--tree DIR]

UniXNU's own `unixnu build payload` writes the volume with mtools (scripts/unixnu/payload.py); upstream
package/Scripts/mk-gpt-disk.py falls back to the pyfatfs module when mtools is absent, and so does this script
(pip install pyfatfs). UniXNURamDiskDxe publishes the volume from /chosen linux,initrd-start/end, so its size is
a whole number of 512-byte sectors.

Without --tree the volume holds only EFI/UniXNU/unixnu.toml: UniXNU's built-in entries (Library/UniXNULib/
Config.c mBuiltinConfig) with timeout = -1, so the menu waits for the buttons instead of returning to the
firmware after 10 s. With --tree, that directory is copied as well (later files win).
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

MENU_CONFIG = """\
version = 1

# UniXNU's built-in entries, without the countdown: the menu waits for a button.
[boot]
timeout = -1

[ui]
title = "UniXNU blueline"

[[entry]]
id = "exit"
title = "Return to firmware"
type = "action"
action = "exit"

[[entry]]
id = "reset"
title = "Restart"
type = "action"
action = "reset"

[[entry]]
id = "shutdown"
title = "Power off"
type = "action"
action = "shutdown"

[[entry]]
id = "tools"
title = "Tools"
type = "action"
action = "tools"
"""


def populate(image, tree):
    from pyfatfs.PyFatFS import PyFatFS

    fat = PyFatFS(str(image))
    try:
        for path in sorted(tree.rglob("*")):
            rel = "/" + path.relative_to(tree).as_posix()
            if path.is_dir():
                fat.makedirs(rel, recreate=True)
            else:
                fat.makedirs(str(Path(rel).parent.as_posix()), recreate=True)
                with fat.openbin(rel, "wb") as out:
                    out.write(path.read_bytes())
    finally:
        fat.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--tree", type=Path, help="extra files for the volume (copied after the menu config)")
    ap.add_argument("--mib", type=int, default=16, help="volume size in MiB (FAT16)")
    ap.add_argument("--label", default="BLUELINE")
    args = ap.parse_args()
    try:
        from pyfatfs.PyFat import PyFat
    except ImportError:
        sys.exit("pyfatfs missing: pip install pyfatfs")

    with tempfile.TemporaryDirectory(prefix="blueline-payload-") as name:
        stage = Path(name)
        (stage / "EFI/UniXNU").mkdir(parents=True)
        (stage / "EFI/UniXNU/unixnu.toml").write_text(MENU_CONFIG, newline="\n")
        if args.tree:
            shutil.copytree(args.tree, stage, dirs_exist_ok=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.unlink(missing_ok=True)
        with args.output.open("wb") as f:
            f.truncate(args.mib << 20)
        fat = PyFat()
        fat.mkfs(str(args.output), PyFat.FAT_TYPE_FAT16, size=args.mib << 20, label=args.label[:11],
                 volume_id=0x424C4E33)
        fat.close()
        populate(args.output, stage)

    # Read the files back through pyfatfs: a volume that does not hold them must not reach the phone.
    from pyfatfs.PyFatFS import PyFatFS
    fat = PyFatFS(str(args.output), read_only=True)
    try:
        got = fat.readbytes("/EFI/UniXNU/unixnu.toml").decode()
    finally:
        fat.close()
    if got != MENU_CONFIG and not args.tree:
        sys.exit("unixnu.toml does not read back")
    size = args.output.stat().st_size
    if size % 512:
        sys.exit(f"{args.output} is not a whole number of sectors")
    print(f"{args.output}: {size} bytes, FAT16 '{args.label[:11]}', EFI/UniXNU/unixnu.toml {len(got)} bytes")


if __name__ == "__main__":
    main()
