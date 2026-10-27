#!/usr/bin/env python3
"""Write bluelinePkg's Include/APRIORI.inc and Include/DXE.inc from polaris's (Mu-Silicium SDM845) lists.

    python scripts/gen-blueline-dxe-inc.py ../uefi-blueline

The driver order is polaris's. Changes:
  - polaris module names become the Pixel 3 XBL module names (scripts/extract-blueline-xbl.py output);
  - every stock binary is built only with BLUELINE_VENDOR_HW = 1 (as sheng's SHENG_VENDOR_HW);
  - the display: the stock DisplayDxe chain with BLUELINE_VENDOR_HW = 1 and USE_CUSTOM_DISPLAY_DRIVER = 1,
    otherwise SimpleFbDxe on the framebuffer ABL left scanning out;
  - DXE.inc adds UniXNU's RAM disk driver and DarwinBoot (as sheng's DXE.inc), and for BLUELINE_VENDOR_HW = 0 the
    FDT boot-memory driver and the SPMI key driver.
"""

import sys
from pathlib import Path

NAMES = {
    "DALSYSDxe/DALSYSDxe": "DALSys/DALSys", "HWIODxe/HWIODxe": "HWIODxeDriver/HWIODxeDriver",
    "ChipInfoDxe/ChipInfoDxe": "ChipInfo/ChipInfo", "PlatformInfoDxe/PlatformInfoDxe": "PlatformInfoDxeDriver/PlatformInfoDxeDriver",
    "HALIOMMUDxe/HALIOMMUDxe": "HALIOMMU/HALIOMMU", "TzDxe/TzDxeLA": "TzDxe/TzDxe", "TLMMDxe/TLMMDxe": "DALTLMM/DALTLMM",
    "SPMIDxe/SPMIDxe": "SPMI/SPMI", "PmicDxe/PmicDxeLa": "PmicDxe/PmicDxe",
    "QcomChargerDxe/QcomChargerDxeLA": "QcomChargerDxeLA/QcomChargerDxeLA", "RNGDxe/RngDxe": "RngDxe/RngDxe",
}
VENDOR = "!if $(BLUELINE_VENDOR_HW) == 1"
DISPLAY_START = "!if $(USE_CUSTOM_DISPLAY_DRIVER) == 1"


def convert(text, extra):
    out, in_vendor, in_display = [], False, False
    for line in text.splitlines():
        stripped = line.strip()
        for a, b in NAMES.items():
            line = line.replace(f"QcomPkg/Drivers/{a}.inf", f"QcomPkg/Drivers/{b}.inf")
        line = line.replace("Binaries/polaris/", "Binaries/blueline/")
        if stripped == DISPLAY_START:
            if in_vendor:
                out.append("!endif")
                in_vendor = False
            in_display = True
            out.append("!if ($(BLUELINE_VENDOR_HW) == 1) && ($(USE_CUSTOM_DISPLAY_DRIVER) == 1)")
            continue
        if in_display:
            if stripped == "!endif":
                in_display = False
            out.append(line)
            continue
        vendor_line = "Binaries/blueline/" in line
        if vendor_line and not in_vendor:
            out.append(VENDOR)
            in_vendor = True
        elif not vendor_line and in_vendor and stripped:
            out.append("!endif")
            in_vendor = False
        if stripped == "}" and extra:
            out.extend(extra)
        out.append(line)
    if in_vendor:
        out.append("!endif")
    if extra and not any(l.strip() == "}" for l in text.splitlines()):
        out.extend(extra)
    return "\n".join(out) + "\n"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    uefi = Path(sys.argv[1])
    src = uefi / "Platforms/Xiaomi/polarisPkg/Include"
    dst = uefi / "Platforms/Google/bluelinePkg/Include"
    (dst / "APRIORI.inc").write_text(convert((src / "APRIORI.inc").read_text(), []), newline="\n")
    extra = [
        "",
        "  # UniXNU (as sheng): the RAM disk driver publishes the boot image ramdisk (the payload volume) as Block I/O;",
        "  # the XNU loader is part of the firmware, since a driver loaded from a volume is refused by image verification.",
        "  INF UniXNUPkg/Driver/UniXNURamDiskDxe/UniXNURamDiskDxe.inf",
        "  INF DarwinBootPkg/Driver/DarwinBoot.inf",
        "!if $(BLUELINE_VENDOR_HW) == 0",
        "  # Without the stock drivers no RAM partition table is read: DRAM comes from the FDT ABL passes in x0.",
        "  INF bluelinePkg/Drivers/BluelineBootMemoryDxe/BluelineBootMemoryDxe.inf",
        "  # Nor is there a ButtonsDxe: power and volume keys straight from the PM8998 over the SPMI arbiter.",
        "  INF bluelinePkg/Drivers/BluelineKeyInputDxe/BluelineKeyInputDxe.inf",
        "!endif",
    ]
    (dst / "DXE.inc").write_text(convert((src / "DXE.inc").read_text(), extra), newline="\n")
    for name in ("APRIORI.inc", "DXE.inc"):
        text = (dst / name).read_text()
        if "polaris" in text or text.count("!if") != text.count("!endif"):
            sys.exit(f"{name}: conversion left polaris names or unbalanced conditionals")
        print(f"{dst / name}: {text.count('INF ')} modules")


if __name__ == "__main__":
    main()
