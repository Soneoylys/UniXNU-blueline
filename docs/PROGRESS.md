# blueline progress

## Inputs (2026-10-26)

| Input | Source | SHA-256 |
| --- | --- | --- |
| `bootloader-blueline-b1c1-0.4-7617406.img` | factory `blueline-sp1a.210812.016.c2-factory-fa981d87.zip` | `85a788ec…d5dc6` |
| `xbl.img` (FBPK entry) | the bootloader image | `b9bb2fc4…527c1` |
| `boot.img` | `image-blueline-sp1a.210812.016.c2.zip` (stored inside the factory zip) | `5c1ce19e…b506e` |
| `dtbo.img` | same | `ff7bd6da…ab42d` |

Findings from them:

- The XBL's UEFI firmware volume is the ELF segment loaded at **0x9FC00000** (0x268000 bytes): the same FD base as
  Mu-Silicium's SDM845 boards (polaris, enchilada). Its main FV is a GUIDed section `1D301FE9-…` holding a **gzip**
  stream (Qualcomm), not LZMA.
- 80 modules: DxeCore, the Qualcomm drivers (UFSDxe, ClockDxe, PmicDxe, DisplayDxe, ButtonsDxe, TzDxe, RngDxe, USB …),
  Pixel-only VerifiedBootDxe, CitadelTransportSpiDxe (Titan M), QcomBds, QcomChargerApp; raw `uefiplat.cfg`,
  `SecParti.cfg`, `QcomChargerCfg.cfg`, `BDS_Menu.cfg`, logos.
- `uefiplat.cfg` [MemoryMap] matches polaris's except HLOS1/HLOS2, MLVM at 0xA0000000, Metrics, BootDeviceInfo and
  Kernel Extended at 0xB0000000; TzAppsRegnSize is 0x2200000.
- The stock boot image is header v2 (kernel LZ4, dtb section at 0x1F00000 with two DTBs: SDM845 v2.1 and v2,
  msm-id 0x141/0x20001 and 0x141/0x20000, board-id 0/0). Retail blueline is dtbo overlay 8, *B1 DVT1.1*,
  board-id 0x21505. Offsets: kernel 0x8000, ramdisk 0x1000000, tags 0x100, 4 KiB pages.
- No-map carve-outs of the stock DTB, added to bluelinePkg's memory map (uefiplat.cfg leaves them out):
  hyp 0x85700000, xbl 0x85E00000, removed 0x85FC0000–0x88F00000 (around AOP CMD DB and SMEM; holds TZApps
  0x86D00000), PIL 0x8AB00000–0x97C00000 (qseecom, camera, ipa, gpu, adsp, wlan, modem, video, cdsp, mba, slpi,
  spss). The blueline overlay adds ramoops at 0xA1800000–0xA1C11000, inside MLVM.

## Firmware variants

All three embed UniXNU, UniXNUDxe, UniXNURamDiskDxe and DarwinBoot (`refs/UniXNU-main`, UniXNU `ae68700`).

| Variant | `BLUELINE_VENDOR_HW` | Display | DRAM | Keys | UFS |
| --- | --- | --- | --- | --- | --- |
| `mu-simplefb` | 0 | SimpleFbDxe on ABL's framebuffer 0x9D400000 | ABL's FDT (BluelineBootMemoryDxe) | none | no |
| `stock-simplefb` | 1 | SimpleFbDxe | RamManagerDxe (SMEM RAM table) | ButtonsDxe | UFSDxe |
| `stock-vendor` | 1 | stock DisplayDxe (+ CPRDxe, DisplayReEnablerDxe) | RamManagerDxe | ButtonsDxe | UFSDxe |

`mu-simplefb` is sheng's working configuration (on sheng the stock drivers blanked the panel). `stock-*` is polaris's.

Each variant is packed twice: `.img` is header v1 with the DTB appended to the kernel (Mu-Silicium's layout, as the
2021 edk2-Pixel3 port), `-v2.img` is header v2 with the DTB in the dtb section (the stock layout).

## Test on the phone (not done yet)

Needs: an unlocked bootloader (`fastboot flashing unlock` wipes the phone; that is the owner's decision),
`fastboot` on the host. `fastboot boot` loads the image into RAM once: no partition changes, and a long press of
power returns to Android.

1. Power off, hold power + volume down for the bootloader screen, connect USB.
2. `fastboot getvar all` and keep the output (bootloader version, `partition-size:userdata`, slot).
3. Try in this order, one at a time, and photograph the screen after ~20 s:
   ```
   fastboot boot build/boot/blueline-unixnu-mu-simplefb.img
   fastboot boot build/boot/blueline-unixnu-mu-simplefb-v2.img
   fastboot boot build/boot/blueline-unixnu-stock-simplefb.img
   fastboot boot build/boot/blueline-unixnu-stock-vendor.img
   ```
   The DEBUG build writes its log to the screen (FrameBufferSerialPortLib); the UniXNU menu prints `UNIXNU:` lines.
4. Report per image: what fastboot printed (`OKAY`, or an error such as a rejected header or DTB), whether the screen
   changed, the last readable log lines, and whether it reset back to the bootloader.

| What happens | Most likely cause |
| --- | --- |
| fastboot error on `boot` | ABL refuses the header or the DTB: try the other header version |
| bootloader screen stays, no reset | ABL did not jump, or BootShim's copy to 0x9FC00000 hung |
| black screen, then reset | SEC or DXE fault before the framebuffer log; next build: earlier log lines |
| log stops at a Qualcomm driver | that stock driver: try `mu-simplefb` |
| UniXNU menu | stage 2 reached; next is the payload volume and DarwinBoot |

## Device runs

### Run 1 (2026-10-26): `blueline-unixnu-mu-simplefb.img`, refused by fastboot

```
Sending 'boot.img' (1040 KB)    OKAY
Booting                         FAILED (remote: 'Error verifying the received boot.img: Invalid Parameter')
```

Cause, read from the stock ABL (`abl.img` → FV at 0x9FA00000 → LinuxLoader, disassembled at 0x11FEC–0x121A0):
after decompressing the kernel to a 2 MiB-aligned address + 0x80000, LinuxLoader checks the ARM64 header:
magic `ARM\x64`; `image_size`, if non-zero, at most the load window (else EFI_BUFFER_TOO_SMALL, "Kernel image is
too big"); **`text_offset` must be 0x80000** (else EFI_INVALID_PARAMETER, "Kernel TextOffset does not match").
Mu-Silicium's BootShim writes FD_BASE (0x9FC00000) there. B1c1FastbootApp prints that status as
"Error verifying the received boot.img". The same parser takes boot header versions 0, 1 and 2.

Fix: BootShim.S takes an optional `TEXT_OFFSET` (default FD_BASE, unchanged for other boards); blueline.toml sets
`[boot_shim] text_offset = 0x80000` and `scripts/package-blueline-boot.py` passes it. The shim is position
independent, so only the bootloader reads the field. All six images were repacked; decompressing each and
replaying those checks passes (text_offset 0x80000, image_size 0x3000C0 = decompressed size).

### Run 2 (2026-10-26): text_offset 0x80000, the UniXNU menu

The phone showed the UniXNU menu (ABL accepted the header v1 image; BootShim, SEC, DXE, the display and UniXNU all
ran), then returned to the firmware, which reported that nothing was found and powered off. The buttons worked in
the menu.

That is UniXNU's built-in configuration, used when no payload volume exists: its default entry is "Return to
firmware" after a 10 s timeout (Library/UniXNULib/Config.c mBuiltinConfig); the firmware's boot manager then finds
no boot option.

Next images (`blueline-unixnu-<variant>-menu.img`): the same firmware with a 16 MiB payload volume as the ramdisk
(`scripts/build-blueline-payload.py`, FAT16 via pyfatfs as upstream mk-gpt-disk.py does without mtools). It holds
only `EFI/UniXNU/unixnu.toml`: the built-in entries with `timeout = -1`, so the menu waits for a button. This also
tests UniXNURamDiskDxe and the payload path (ABL's /chosen linux,initrd-start/end). `mu-simplefb` has no
ButtonsDxe: with no input device UniXNU still counts down `fallback_timeout`.

### Run 3 (2026-10-26): menu payload

- `mu-simplefb-menu`: the menu came up (the payload volume was found: the countdown was gone) but the volume keys did
  nothing. A line "Failed to Locate STI Device Path" showed before the menu, as in run 2. That line is harmless:
  SiliciumPkg BootManagerLib looks for a keypad for its volume-up boot-menu hotkey and finds none.
  This firmware has no key driver at all (no ButtonsDxe, KeypadDxe or SPMI driver in its volume). The user reports
  the keys worked in run 2; the built-in menu of run 2 also rotates its highlight every 2 s (`rotate = 2`), which
  the menu payload does not set. Either way it needed a key driver.
- `stock-simplefb-menu`: the DEBUG log ran on screen, then the text glitched and the phone reset. The last lines
  were not captured; a photo of them is needed to name the stock driver that faults.

Fix: `BluelineKeyInputDxe` (Mu-only variant), sheng's ShengKeyInputDxe with the SDM845 facts from the stock DTB:
SPMI arbiter v5 (core 0xC440000, APID map at +0x900, observer 0xE600000 + 0x10000·EE + 0x80·APID), PM8998 PON
0x800 RT_STS bit 0 = power, bit 1 = volume down (RESIN), PM8998 GPIO6 (peripheral 0xC5) active low = volume up.
Volume up/down = Up/Down, power = Enter. A host test against a simulated v5 arbiter passes
(`Drivers/BluelineKeyInputDxe/Test/keys_host.c`).

### Run 4 (2026-10-26): `mu-simplefb-menu` with BluelineKeyInputDxe

The menu came up, the keys worked, and nothing crashed. The harmless "Failed to Locate STI Device Path" line still shows.
The SDM845 SPMI arbiter v5 path and the PM8998 PON/GPIO6 key bits are confirmed on hardware.

### Run 5 (2026-10-26): `stock-vendor-menu`, crashed

The user reported only that it crashed; no log lines were captured. Both stock variants (`BLUELINE_VENDOR_HW = 1`)
now fail on the phone, so `mu-simplefb` is the base going forward. The stock drivers are only worth debugging if a later
step needs one of them (UFS from UEFI, or clocks/power that XNU's drivers find off); a photo of the last log lines
comes first then.

## Kernel builds

### Kernel 1 (2026-10-26): format 2, Intel build Mac

`./unixnu build kernel -D ../UniXNU-blueline` on UniXNU `f85b5a6` (upstream), UniXNU-blueline `d799c7a`, UniXNU-sdm845-common
`d2a5f3b7`; macOS 26.7.1 (x86_64), Xcode 26.6, iPhoneOS 26.5 SDK, MacPorts LLVM and ld.lld 19.1.7. Six and a half minutes
with 32 jobs.

- `kernel.macho` 51879936 bytes, sha256 `eabccbc2ef6038389e0a264f13f59fb95e16d2354d8bc580071a7cdfb6af7d05`, Darwin 23.6.0
  DEVELOPMENT_ARM64_BCM2837, prelinked on the 21H461 kernelcache. The prelink report calls it an experimental candidate
  (`loadable=false`, `runtime_qualified=false`), as the fajita kernel that reached BSD init on the Mi 8.
- ArmCpu comes from UniXNU-sdm845-common (`armcpu-timer-intid` is in the image).
- The first-pass link error (`_mockfs_mountroot` undefined, `logs/kernel-bootstrap.log`) is the deliberate bootstrap
  pass that only collects compile records (scripts/unixnu/kernel.py).
- First attempt failed because the SoC patches had CRLF line endings (copied from a Windows checkout); fixed in the SoC
  repository.

Not run on the phone: the payload needs the measured DRAM size (dts/blueline.dts, `[payload] dram_size`).

## Open items

- DRAM size (`[payload] dram_size`) and the userdata LBA range: read on the phone.
- Root device: an XNU driver for the SDM845 UFS host (UFS 2.1, 0x1d84000); NativeUfs is SM8550-specific.
- Kernel build: macOS (Xcode) or a GitHub macOS runner, as `UniXNU-pocket-max/.github/workflows/build-kernel.yml`.
- Keys in `mu-simplefb`: done (BluelineKeyInputDxe, run 4).
