# UniXNU-blueline

> [!WARNING]
> **This repository is entirely AI-generated** (Anthropic Claude Code and Codex, under the direction of the owner), as
> UniXNU-pocket-max. No line has been reviewed by a human firmware or kernel engineer. The firmware has run on a
> Pixel 3; one-core XNU reaches userland with persistent writable Data, and the A630 reaches CP/fence/ZAP checks and
> Metal accelerator registration. Userspace GPU rendering and presentation are not yet verified (docs/PROGRESS.md).
> Boot images use temporary `fastboot boot`, but XNU writes persistent Data and diagnostic logs inside `darwin`.

Experimental [UniXNU](https://github.com/UniXNU/UniXNU) device repository for the **Google Pixel 3 (blueline)**:
Snapdragon 845 (SDM845), 4 GB RAM, UFS 2.1, 1080x2160 OLED panel.

## Boot route

`fastboot boot` of an Android boot image whose kernel is gzip(BootShim + UEFI firmware volume) with the stock DTB
appended. The firmware is a Mu-Silicium port, `Platforms/Google/bluelinePkg`, on a branch `blueline` of
[uefi-sheng](https://github.com/UniXNU/uefi-sheng) (Mu-Silicium with UniXNU's SEC and boot-shim changes), checked out
at `uefi-blueline/`. It embeds UniXNU, UniXNUDxe, UniXNURamDiskDxe and DarwinBoot, as sheng's firmware does.

There was no Mu-Silicium port for blueline. The closest earlier work is a 2021 Windows-on-ARM EDK II port,
[edk2-Pixel3](https://github.com/5-Super-Rookie-5/edk2-Pixel3) (from zhuowei's Pixel 3 XL port), which proved that the
stock Pixel 3 XBL drivers boot under a custom EDK II (UFS, clocks, WinPE). This port takes its drivers from the
current stock firmware instead: the XBL of bootloader `b1c1-0.4-7617406` (the last Pixel 3 factory image,
SP1A.210812.016.C2), extracted with `scripts/extract-blueline-xbl.py`, and the memory map from that XBL's
`uefiplat.cfg` plus the no-map carve-outs of the stock device tree.

## Current stage

| Stage | State |
| --- | --- |
| Stock inputs: bootloader image, boot.img, dtbo.img (range requests, ~38 MB of the 2 GB zip) | done, pinned by SHA-256 |
| XBL drivers and `uefiplat.cfg` extracted (80 modules, all CRCs and hashes recorded) | done |
| `bluelinePkg` firmware volume builds on Windows (three variants) | done |
| Boot images for `fastboot boot` (header v1, appended DTB, text_offset 0x80000) | done |
| UniXNU menu, payload volume and keys on the phone (Mu-only firmware) | **works** (runs 2-4); the stock-driver variants crash |
| Format 2 on UniXNU-sdm845-common | iPadOS 18.7.11; latest candidate is run 48 / kernel 39, built on the owner's Mac; phone result pending |
| Boot, root and Data | one-core userland runs; writable `/var` survives reboot (run 29 readback) |
| CPU | run 48 retains the run-47 eight-core payload and SGI-1 IPIs; stability/delivery still need readback; board defaults remain one core |
| Kernel display | 22H384 GopFramebuffer registers a main CADisplay (run 30); GPU-only presentation is not yet verified |
| Rendering | owner selected software rendering for current bring-up (`CPU_COPY=1`); GPU work is deferred |
| Input | kernel button/touch paths are implemented; input and wake behavior need current-candidate readback |

Run 47 reset to Android with watchdog boot reason after about a minute at the handoff screen. Run 48 removes the new
active interrupt probe from logger startup while retaining SGI 1 and the rest of that candidate. Full build, binary,
payload and archive checks pass; a phone test is still needed. Root, Data seed/UUID and store geometry are unchanged.

[docs/PROGRESS.md](docs/PROGRESS.md) has the variants, the test procedure and what to report.
[docs/BOOT-ARGS-AUDIT.md](docs/BOOT-ARGS-AUDIT.md) compares the Sheng/Pocket Max startup settings and Pixel 3 CPU evidence.
[docs/PERSISTENT-DATA.md](docs/PERSISTENT-DATA.md) records the Data seed, bounded overlay and AMFI change.
[docs/GPU-A630.md](docs/GPU-A630.md) records the GPU integration and remaining verification;
[porting.toml](porting.toml) is the structured porting record.

## Contents

| Path | Purpose |
| --- | --- |
| `board.toml`, `configs/blueline_defconfig` | Format 2 board description and first kernel configuration for `unixnu` |
| `dts/blueline.dts`, `feeds.toml` | Board hardware description (includes `sdm845.dtsi`) and the pinned SoC repository |
| `patches/xnu/series`, `package/` | Board XNU diagnostics, GPU/display integration, logger and archive recipes (`kits/blueline`) |
| `porting.toml`, `AGENTS.md`, `proprietary-files.txt` | Porting record, agent guide, pinned stock firmware inputs |
| `scripts/fetch-blueline-factory.py` | Fetch single members of the factory zip (nested image zip too) over HTTP range requests |
| `scripts/unpack-fbpk.py` | Pixel bootloader image (FBPK v1) → xbl, abl, tz, ... and the FBPT partition tables, CRC-checked |
| `scripts/extract-blueline-xbl.py` | XBL ELF → firmware volume → drivers, depex and raw files in the Device-Binaries layout |
| `scripts/import-blueline-uefi.py` | Stage the drivers and the stock DTB into the firmware checkout |
| `scripts/gen-blueline-dxe-inc.py` | bluelinePkg's APRIORI/DXE lists from polaris's (SDM845), with Pixel 3 module names |
| `scripts/build-blueline-payload.py` | UniXNU payload volume (boot image ramdisk) without mtools: a no-timeout menu config, optional extra tree |
| `scripts/package-blueline-boot.py` | Boot image for `fastboot boot`, verified by unpacking |
| `scripts/check-blueline.py` | Board preflight: refuses copied sheng/Pocket Max values and unpinned inputs |
| `scripts/fdt.py` | Small FDT and dtbo reader (there is no dtc on the Windows host) |
| `tools/windows/build-basetools.py` | EDK II BaseTools C programs (GenFv, GenFfs, GenSec, LzmaCompress, VfrCompile, ...) with zig |
| `tools/windows/build-firmware.ps1` | The bluelinePkg EDK II build on Windows, with Mu-Silicium's Mu_Basecore patches applied and reverted |

## Setup

```sh
git clone https://github.com/Soneoylys/UniXNU-blueline.git unixnu_blueline
cd unixnu_blueline
git clone -b blueline https://github.com/Soneoylys/uefi-blueline.git uefi-blueline      # private, as this one
git -C uefi-blueline submodule update --init --depth 1 -- Common/Mu Common/Mu_OEM_Sample Silicium-ACPI Silicon/Silicium/OpensslPkg/Library/OpensslLib/openssl
git clone --depth 1 https://github.com/UniXNU/UniXNU.git refs/UniXNU-main               # UniXNUPkg and DarwinBoot
git -C refs/UniXNU-main submodule update --init --depth 1 -- drivers/DarwinBoot
```

The SoC layer is [UniXNU-sdm845-common](https://github.com/Soneoylys/UniXNU-sdm845-common), pinned in `feeds.toml`.
`unixnu fetch` checks it out; `local.toml` (see `local.toml.example`) can point at a local checkout instead.

`.cache/`, `build/`, `uefi-blueline/` and `refs/` are not tracked here. The firmware build of this repository used
UniXNU `ae68700` and uefi-blueline `d3c33957` (uefi-sheng `20ac23d5` plus bluelinePkg).

## Build (Windows)

```powershell
python scripts/fetch-blueline-factory.py "bootloader-*.img" "image-*.zip/boot.img" "image-*.zip/dtbo.img"
python scripts/extract-blueline-xbl.py .cache/firmware/bootloader/xbl.img .cache/firmware/xbl-extract
python scripts/import-blueline-uefi.py uefi-blueline
.\tools\windows\build-firmware.ps1 -NoVendor -Display simplefb
python scripts/package-blueline-boot.py --fd build/firmware/blueline-mu-simplefb-debug.fd -o build/boot/blueline-unixnu-mu-simplefb.img
```

Between the first two steps: `python scripts/unpack-fbpk.py .cache/firmware/bootloader-blueline-b1c1-0.4-7617406.img .cache/firmware/bootloader`.
The EDK II tool directory is `C:\Users\user\edk2-win2` (UniXNU-pocket-max `tools/windows/setup-edk2.py`, then
`tools/windows/build-basetools.py`).

## Kernel (macOS)

From a UniXNU checkout on the Mac, with this repository beside it as `../UniXNU-blueline`:

```sh
./unixnu defconfig blueline_defconfig -D ../UniXNU-blueline
./unixnu fetch
./unixnu status --json
./unixnu build kernel
```

## Private blobs

The Pixel 3 firmware, boot image and extracted drivers live in `.cache/firmware/` and the firmware checkout's
`Binaries/blueline/`; neither is tracked.

## License

BSD-2-Clause-Patent, as UniXNU. The Pixel 3 firmware is not part of this repository.
