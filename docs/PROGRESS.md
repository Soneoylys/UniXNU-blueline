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

### Run 6 (2026-10-27): `mu-simplefb-xnu18` (first XNU attempt), no DarwinBoot window

fastboot accepted the 85 MB image, the menu booted the `xnu` entry and DarwinBoot loaded the kernel and ADT from the
80 MiB payload, then stopped: `NO WINDOW 40000000 BIG RUN 8600000 @ A7A00000` (as reported, with zeros restored):
the 1 GiB window did not fit, and the largest free run was 134 MiB, exactly 0xA7A00000..0xB0000000 between
BootDeviceInfo and Kernel Extended. So no RAM above 0xB8000000 reached UEFI:

- the static memory map (MemoryMapLib, from uefiplat.cfg) ends at Kernel Extended (0xB8000000);
- BluelineBootMemoryDxe (copied from sheng) skipped every piece whose end is above 4 GiB instead of clipping it, so
  ABL's DRAM bank, which runs to 0x180000000, added nothing past 0xB0000000; it also stopped at 1 GiB in total.

Sheng gets its window from DRAM declared in its static map. Fix (uefi-blueline, uncommitted): the upper half of the
4 GiB part, 0x100000000 + 2 GiB, is a static Conventional region; BluelineBootMemoryDxe clips at 4 GiB, adds up to
2 GiB, and keeps ABL's device tree blob and the initrd (the payload, which UniXNURamDiskDxe reads in place) out of
what it adds. 0xB8000000..0x180000000 (3.125 GiB) can then be one run; `owned_memory_size` is 2 GiB.

### Run 7 (2026-10-27): 2 GiB window, still no window

`NO WINDOW 80000000 BIG RUN C8000000 @ B8000000`, then the free list (zeros restored as reported):

```
FREE 89000000 + 1A00000     between TZ Reserved and PIL (added by BluelineBootMemoryDxe)
FREE 97C00000 + C0E000      what is left of DXE Heap
FREE A7A00000 + 8600000     between BootDeviceInfo and Kernel Extended
FREE B8000000 + 48000000    0xB8000000..4 GiB (added by BluelineBootMemoryDxe)
FREE 100000000 + 80000000   the static region
```

Both fixes worked (the RAM reaches UEFI, 3.125 GiB from 0xB8000000), but the last two runs are separate memory map
entries: DarwinBoot's BIG RUN joins adjacent entries (RunEnd), while DxeCore's AllocatePages converts only inside one
entry ("ConvertPages: range covers multiple entries", MdeModulePkg/Core/Dxe/Mem/Page.c), and neither entry holds
2 GiB. Fix: MemoryMapLib declares 0xB8000000 + 0xC8000000 as one static region (the driver then has no hole there),
and BluelineBootMemoryDxe allocates the initrd pages that lie in free memory, in case ABL loaded the payload into it.

### Run 8 (2026-10-27): ConvertPages errors

The screen showed `ConvertPages: Incompatible memory types, the pages to allocate have been allocated` (DxeCore,
MdeModulePkg/Core/Dxe/Mem/Page.c). That is run 8's ReserveInitrd: it allocated the 80 MiB initrd one page at a time,
and every page that was not free (the initrd is in memory UEFI already holds, probably Kernel Extended) failed and
printed that line: about 20,000 of them to the framebuffer log. Fix: ReserveInitrd reads the memory map and
allocates only the parts of the initrd that lie in conventional memory, one call per entry; nothing fails or prints.
Whether the boot went on after the errors was not reported.
Run 9 image: firmware `aaa3e4e7…4f4b`, same payload (2 GiB window), boot image sha256 `86bc7ac6…3e2d`.

### Run 9 (2026-10-27): XNU runs, panic at IOKit start

First XNU execution on blueline. DarwinBoot allocated the 2 GiB window (green band), the kernel entry probe ran
(blue band), and XNU got through arm_init, the VM (`pages=0x79cc6`, `free=0x79a86`: about 1.9 GiB managed) and the
scheduler to kernel_bootstrap_thread, then drew its panic on the screen:

```
Unexpected fault in kernel static region at pc 0xfffffff0078daa5c, lr 0xfffffff0078daa34
esr 0x96000007 (data abort, translation fault level 3, read)  far 0xfffffff006ae52e0  x21 0xfffffff006ae52d0
```

Symbolized (atos on the unstripped kernel): `OSMetaClass::postModLoad` (OSMetaClass.cpp:901) ←
`OSRuntimeInitializeCPP` ← `OSlibkernInit` ← `InitIOKit` ← `PE_init_iokit` ← `kernel_bootstrap_thread`. Line 901
walks `superClassLink`; the bad superclass is `__ZN16IOAESAccelerator10gMetaClassE = 0xfffffff006ae52d0`, an absolute
symbol in no segment of the image. SoftwareAES's `NativeAES` subclasses Apple's IOAESAccelerator, and
`build-software-aes-bindings.py` binds it to `scripts/data/native-aes-original-bindings.S`: addresses recorded in the
21H461 kernelcache. The 22 absolute symbols of the kernel are all these.

Fix (UniXNU `ipados-18` branch, uncommitted): `scripts/data/native-aes-original-bindings-22H384.S`, mapped from the
21H461 file (`~/blueline/ios18-tools/aesvt.py`, `aesmap.py` on the Mac):

- IOAESAccelerator's vtable has 181 slots in both kernelcaches (same ABI); the 19 virtual methods map by slot, and
  agree with masked code matches where the code is unchanged;
- C2 at 0xfffffff004a41060 (stores the new vtable), D2 at 0xfffffff004a41084;
- gMetaClass 0xfffffff006b91250 (the address `getMetaClass` returns; the same method gives the recorded value in
  21H461), inside `__PRELINK_DATA`.

`build-software-aes-bindings.py` takes `--recorded`, and the iOS 18 profile's `archives` passes the 22H384 file.
Kernel 3: `kernel.macho` sha256 `491ccb4d…9def` (gMetaClass and C2 at the 22H384 addresses); payload `c008bfbd…c2f9`;
run 10 image sha256 `634d4e5b…32df` (same firmware as run 9).

### Run 10 (2026-10-27): past IOKit start, IRQ without a handler in bsd_init

The AES fix worked: the kernel got through `OSlibkernInit`/IOKit start into `bsd_init`. Panic:
`Kernel instruction fetch abort at pc 0x0, lr 0xfffffff00747080c` (esr 0x86000005). Symbolized: `sleh_irq`
(sleh.c:2537) ← `fleh_irq` ← `kmem_alloc_guard_internal` ← `zone_expand_locked` ← `zalloc_item` ←
`mz_composite_build` ← … ← `m_getcl` ← `dlil_init` ← `bsd_init` ← `kernel_bootstrap_thread`.

`sleh_irq` loads `cpu_data->interrupt_handler` (+0xd8) into x8 and `blr x8`: x8 and the target, refCon, nub and
source (x0..x3) are all 0, so no handler had been installed on the CPU when an IRQ arrived. BCM2837 has no
`USE_APPLEARMSMP`, so the handler is the one ArmCpu installs (`IOCPUInterruptController::enableCPUInterrupt` →
`ml_install_interrupt_handler`) when it starts during IOKit matching, which runs alongside bsd_init. What raised the
IRQ is open: DarwinBoot calls ExitBootServices, whose Mu GICv3 handler clears `ICC_IGRPEN1_EL1` and the
distributor enables (it leaves the PPIs enabled), Apple's GICv3 FIQ setup (`pexpert/arm/pe_fiq.c`) is compiled out
for BCM2837, and ArmCpu's `prepareGic` quiets the GIC and enables it only after the handler is installed.

Next kernel (4): `patches/xnu/zzz-blueline-irq-diagnostic.patch`. An IRQ without a handler panics with
ICC_IAR1/HPPIR1 (the interrupt ID), IGRPEN1/IGRPEN0, SRE, PMR, RPR, ISR_EL1, CNTV_CTL, CNTP_CTL, ArmCpu's
`armcpu_registered`/`armcpu_gic_ready`, and the interrupted pc.
Kernel 4 `e6e372b0…ff63` (clean rebuild), payload `2605dc57…d2c7`, run 11 image sha256 `d6c7010c…250d`.

### Run 11 (2026-10-27): green, blue and magenta bands, then a reset into Android

No panic text. The magenta band (stage 2, just before the MMU is on) was also reached in runs 9 and 10, where the
panic text was drawn over it. Every XNU panic goes to the patched panic screen and stops, so this reset came from
outside XNU's panic path: a hardware watchdog bite, a TrustZone/hypervisor reset (an XPU or stage-2 violation), or a
fault inside the panic screen itself. The IRQ diagnostic did not report, so either no early IRQ came this time or the
reset came first. Only the GIC is exported under /arm-io, so Apple's watchdog and PMGR drivers have no node to drive.
The reset came 5 s or more after the bands (owner). The APSS watchdog is not the cause: HanaPkg's PlatformSecLib
writes 0 to its enable register (APSS_WDT_TMR1 + 8) in SEC, which is why the UEFI menu could wait indefinitely.
UniXNU's boot-progress page cannot be enabled for blueline (its implementation is sheng's package/BootProgress.c,
and build-xnu-4k-baseline.sh requires the sheng UFS/owner-root/Virtio build for it).

Next kernel (5): `patches/xnu/zzz-blueline-stage-bands.patch` paints full-width bands (1/16 of the panel each) at
kernel stages, counted from the top (band 0 DarwinBoot green, 1 entry blue, 2 MMU magenta):

| Band | Colour | Stage |
| --- | --- | --- |
| 3 | yellow | IOKit start (before PE_init_iokit) |
| 4 | white | PE_init_iokit returned |
| 5 | cyan | bsd_init entered |
| 6 | orange | past dlil_init (run 10's panic point) |
| 7 | pink | root mount (setconf) |
| 8 | gray | an interrupt handler installed (ArmCpu started) |
| 9 | light green | first IRQ handled |
| 10 | red | PEHaltRestart: XNU asked to restart; it now waits forever instead |
| 11 | violet | first idle |
| 12 | white/black | flips every 128 handled IRQs (heartbeat) |

Colours assume XRGB; the band position is what counts.

Kernel 5 `5c5835e1…6047` (both diagnostics), payload `7042f9ee…22fc`, run 12 image sha256 `05cbc872…81cc`.

### Run 12 (2026-10-27): six bands, the last cyan (bsd_init), then a hang and a reset

Bands 0 to 5: DarwinBoot, entry, MMU, IOKit start, PE_init_iokit returned, bsd_init entered. Not band 6 (past
dlil_init), not band 8 (no interrupt handler installed, so ArmCpu had not started and no timer interrupt was taken),
not band 10 (XNU never asked to restart), and no IRQ diagnostic. The kernel stalled between bsd_init's entry and the
end of dlil_init (run 10 panicked inside dlil_init on an early IRQ), and something outside XNU reset the phone seconds
later. Ruled out: the IORTC wait in IOKitInitializeTime (the BCM2837 guard holds; the function is a jump to
clock_initialize_calendar). RAM above 4 GiB is real (the 2021 edk2-Pixel3 port maps 0x80000000 + 0xF4000000).

Next kernel (6): `patches/xnu/zzz-blueline-bsd-steps.patch` turns each `bsd_init_kprintf` step into a block along bands
13 and 14 (32 a row; every fifth yellow, every tenth red), so the photo gives the last step reached.
Kernel 6 `d07482fa…f9fd`, payload `67c9b42e…3160`, run 13 image sha256 `ae192068…fcec`. Block N is drawn right
before the call below (from the disassembly of bsd_init); the last block lit names the step that stalls:

| Block | Next call | Block | Next call | Block | Next call |
| --- | --- | --- | --- | --- | --- |
| 1 | kauth_init | 11 | knote_init | 21 | memorystatus_freeze_init |
| 2 | (process name) | 12 | eventhandler_init | 22 | memorystatus_init |
| 3 | current_uthread | 13 | aio_init | 23 | sysctl_mib_init |
| 4 | chgproccnt | 14 | pshm_cache_init | 24 | (kprintf) |
| 5 | kmem_suballoc | 15 | psem_cache_init | 25 | loopattach |
| 6 | bsd_bufferinit | 16 | nwk_wq_init | 26 | ether_family_init |
| 7 | IOKitInitializeTime | 17 | dlil_init | 27 | net_init_run |
| 8 | vfsinit | 18 | socketinit | 28 | inittodr |
| 9 | proc_uuid_policy_init | 19 | domaininit | 29 | (root mount band) |
| 10 | mbinit | 20 | skywalk_init | 30 | vfs_mountroot |

### Run 13 (2026-10-27): same as run 12, no step blocks

Six bands, no block: the stall is between the cyan band and the first step (kauth_init). Only three calls lie there:
`platform_stall_panic_or_spin` (a no-op without its boot-arg), `throttle_init()` and `printf(copyright)` (the console
path: os_log, then the console ring; the serial side has no UART functions registered, as the ADT has no UART node).

Kernel 7 (`patches/xnu/zzz-blueline-bsd-steps2.patch`) adds three blocks in front: block 1 before
platform_stall_panic_or_spin, 2 before throttle_init, 3 before printf(copyright), 4 before kauth_init; the rest of the
table above moves up by 3. Kernel 7 `be619208…f0ea`, payload `9755852d…3f95`, run 14 image sha256 `3d3c0d32…d7f7`.

### Run 14 (2026-10-27): cyan band, reset after about 5 s

Reported as the cyan band again and a reset about 5 s later; no step block was mentioned. Block 1 is the next call
after the cyan band (verified in the disassembly: blueline_step reads vinfo height, width, depth 32 and base like
blueline_band and draws on bands 13 and 14). Asked the owner for a photo of the whole panel before the reset.

Owner, on a second look: one block. So kernel 7 stalls between block 1 and block 2, which is only
`platform_stall_panic_or_spin(PLATFORM_STALL_XNU_LOCATION_BSD_INIT)` (verified in the disassembly). It spins in
`wfe` (after paniclog_append_noflush, no panic screen) only when `xnu_platform_stall_value & 4`. The variable is
initialized data in `__DATA` (0xfffffff007afd9c8) and is 0 in kernel.macho; arm_init loads it only from
`/chosen/xnu_platform_stall`, which our ADT does not have; DarwinBoot zeroes the window before copying the segments.
So the word was non-zero at run time: kernel memory overwritten after the handoff (a DMA engine left running, or an
overlap of the window with memory in use), which would also explain why the failure moved between runs.

Kernel 8: `patches/xnu/zzz-blueline-stall-diagnostic.patch` panics in that case with the value, its physical address
and the words around it.
Kernel 8 `74b5ac8d…e7a4`, payload `09cd871b…ca7d`, run 15 image sha256 `432e560b…ef02`.

### Run 15 (2026-10-27): cyan band, no block, no panic text, reset

Not even block 1 this time, and no platform-stall panic. The failures keep moving inside the same stretch of kernel
data: a NULL interrupt handler (run 10), the platform-stall switch (run 14), now probably the step counter (a large
value makes blueline_step return without drawing). That fits memory written behind the kernel's back.
Candidate: ABL's USB controller (fastboot download) left running into our firmware, with its DMA event buffer in RAM
that is now inside the kernel window; the host keeps producing events while the cable is in. Test: unplug USB right
after `fastboot boot` reports OKAY, before the UniXNU menu hands off.

Run 15b (USB unplugged right after OKAY): the same, cyan then reset. USB is unlikely.

Since the failure moved with every kernel change and not with firmware changes, the next suspect is the kernel's own
view of memory. iOS 18 is the first OS here that reads `/chosen` dram-base/dram-size (`pmap_first_pnum` =
atop(dram-base), used by the VM for pages below its `vm_pages` array); the ADT said 0x80000000 + 4 GiB while the
kernel runs in DarwinBoot's window near 0xB8000000. Now the memory node and `dram_size` describe the firmware's one
RAM region, 0xB8000000 + 0xC8000000 (the window always lies inside it). Same kernel 8; payload ADT dram-base
0xb8000000, dram-size 0xc8000000; run 16 image sha256 `7878952f…6993`.

### Run 16 (2026-10-27): the same (cyan band, no block, no text, reset)

The dram-base change made no difference. Also ruled out: `/chosen/memory-map` is empty in this payload (no ramdisk,
trust cache or addon), so IODTFreeLoaderInfo frees nothing; DarwinEnter cleans and invalidates the whole window
(`dc civac`) and the I-cache before turning the MMU off. Open question for the owner: did the panic screens of runs
9 and 10 stay up, or also reset after about 5 s? And Android's boot reason after a reset.

Owner: the panic screens of runs 9 and 10 stayed up. A stopped CPU is not reset by itself, so the silent stalls end
in an active reset. A panic spins with interrupts masked; run 14's stall spun in thread context with interrupts
unmasked, so something taken during the stall is the lead. The kernel's only SMC/HVC instructions are
ArmDTPlatform::configure's `sheng-platform-stage` PSCI SYSTEM_RESET probes (boot-arg only, not set) and
ArmCpu::startCPU (CPU_ON, not reached with cpus=1); the Apple hypercalls are compiled out.

Kernel 9 (`patches/xnu/zzz-blueline-status-marks.patch`): eight slots on band 15 (the bottom band), left to right:

| Slot | Meaning |
| --- | --- |
| 0, 1, 2 | at bsd_init entry: FIQ, IRQ, SError pending (ISR_EL1); red = pending, gray = not |
| 3 | an FIQ was taken (magenta) |
| 4 | an SError was taken (orange) |
| 5 | an IRQ was taken (cyan) |
| 6 | the step counter was past 64, i.e. corrupted (magenta) |
| 7 | xnu_platform_stall_value already non-zero at bsd_init entry (red) |

### Correction (2026-10-27): the resets of runs 11 to 16 came from the IRQ diagnostic

Owner: every run since run 11 reset; runs 9 and 10 (panic screens) did not. Run 11 was the first kernel with
`zzz-blueline-irq-diagnostic.patch`, whose panic path read `ICC_IGRPEN0_EL1`. Group 0 belongs to EL3 on this SoC
(SCR_EL3.FIQ), so that read traps to TrustZone, which resets the phone a few seconds later, before the panic screen
draws. The early IRQ of run 10 still arrives; its moment varies, which is why runs 11 to 16 stopped after zero or one
step block. The memory-corruption readings of runs 14 to 16 are withdrawn. The diagnostic now leaves
ICC_IGRPEN0_EL1 alone (the patch keeps only Group 1 and common registers: IAR1, HPPIR1, IGRPEN1, SRE, PMR, RPR).
The dram-base change of run 16 stays (it describes the region the window really comes from).
Kernel 9 `7b4a0282…1613` (no ICC_IGRPEN0_EL1 access anywhere in the image; status marks), payload `62146e2f…7d6d`,
run 17 image sha256 `db0c27f4…f484`.

### Run 17 (2026-10-27): the IRQ is the virtual timer, ArmCpu already started

```
IRQ without a handler: iar1 13 hppir1 13 igrpen1 1 igrpen0 0 sre 1 pmr f0 rpr ff isr 0 cntv_ctl 5 cntp_ctl 2 armcpu 1/1
pc fffffff00743df0c  frames ... fffffff00774b624 (bsd_init, after the cyan band)
```

INTID 0x13 = 19, the EL1 virtual timer (`cntv_ctl` 5: enabled, firing); `armcpu 1/1`: ArmCpu had registered the CPU
and enabled the GIC (IGRPEN1 1), but BootCpuData had no interrupt handler. Cause: xnu-11417's
`ml_processor_register` now ends with `processor_boot(processor)` when `!USE_APPLEARMSMP` (not in xnu-10063). For the
boot CPU that runs `cpu_start` → `cpu_machine_init` → `PE_cpu_machine_init` → `ArmCpu::initCPU(true)` → `enableGic()`
and the timer, inside `ArmCpu::start` and before it creates the IOCPUInterruptController and installs the handler.
The next timer tick finds no handler.

Fix (UniXNU-sdm845-common `drivers/ArmCpu/ArmCpu.cpp`, uncommitted): the boot path of `initCPU` returns while
`gController` is not set yet; `start()` calls `initCPU(true)` itself after installing the handler. Sheng's ArmCpu
(UniXNU-sm8550-common) will need the same change on iOS 18.
Kernel 10 `27e6f045…7462`, payload `3088d1c6…de2b`, run 18 image sha256 `c1befc75…c1f8`.

### Run 18 (2026-10-27): through bsd_init and root mount; AMFI panics "No PMGR?"

The ArmCpu fix works. Bands 0 to 9 and 11: IOKit start, bsd_init, past dlil_init, root mount, interrupt handler
installed, first IRQ handled, first idle; 30 step blocks; status slot 5 (IRQ taken), nothing pending. Then:

```
"AMFI: No PMGR?
" @ConfigurationSettings.cpp:360   frames fffffff004a8f958 fffffff004a74dac ...
```

AppleMobileFileIntegrity's developer-mode setup waits 30 s for a service matching "ApplePMGRNub", casts it to the
ApplePMGR class, reads its always-on register, sets bit 2 and writes it back ("PMGRAON latched"). Nothing publishes
that service here (ApplePMGR is prelinked but has no node to match). AMFI skips the latch ("due to AVP") when
`kern.hv_vmm_present` is set, which xnu reads from `/defaults/vmm-present` (bsd/kern/hvg_sysctl.c).

Workaround: board.toml `[adt.defaults.u32] vmm-present = 1`. The real fix is a PMGR provider: sheng's
ShengPlatformPower (package/ShengPlatformPower.cpp, used by dipper and fajita with CONFIG_XNU_SHENG_PMGR and
sheng-pmgr=2) subclasses the original ApplePMGR with the 21H461 layout (object 0x4b8a8 bytes, readAon/writeAon at
slots 320/321), so it needs an audit of the 22H384 ApplePMGR first. Same kernel 10; run 19 image sha256
`65e7a90e…c190`.

### Run 19 (2026-10-27): the kernel reaches the root mount and fails to find a root device

Reported by the owner: the boot now stops at the root device ("failed to mount root device"). This is the expected
end without a storage driver: the first-boot milestone of XNU to its root wait is reached on iPadOS 18.7.11.
Next: a UFS driver for the SDM845 host and an OS image in the userdata partition (the owner allows replacing only
userdata).

UFS plan (2026-10-27):
1. Userdata geometry from Android: `/sys/class/block/sd*/{uevent PARTNAME,start,size}` (512-byte sectors).
2. Driver: fajita's NativeUfs copy (UniXNU/NativeUfs af785c1 plus the SDM845 apps_smmu stream 0x100 mask 0xf,
   0x80000 window and firmware-entry adoption), placed in UniXNU-sdm845-common as the SoC UFS driver.
3. Storage ABI bindings for 22H384 (IOStorageFamily classes recorded for 21H461, remapped as for the AES class).
4. Read-only milestone: capacity and GPT read and shown on the panel.
5. Userdata image for 22H384 (OS and SystemOS windows), root volume UUID, VerifiedRoot inputs.
6. Flash userdata only (owner's choice) and boot to the iPadOS root mount.

Findings for steps 1 and 3:
- No Android is set up yet; `fastboot getvar all` lists partition sizes but not start offsets, so the userdata
  window comes from the GPT read by the first read-only driver test.
- NativeUfs publishes through VirtioMmioBlock's NativeVirtioBlock, a subclass of IOStorageFamily's
  IOBlockStorageDevice. `build-virtio-mmio-bindings.py` takes every binding from `build/storage-abi/interface-audit.json`
  (per-slot method targets, constructor, base destructor, metaclass, vtable). That audit
  (`audit-storage-interface.py`) is pinned to 21H461: the original kernelcache and IOStorageFamily code hashes,
  190 method slots with 14 pure virtual, 176 bindings, an iOS 17 reference object and a live GDB observation. No
  recorded audit exists in the reference repositories, so 22H384 needs its own static audit (as done for
  IOAESAccelerator): metaclass and vtable from the prelinked kernel, slot count check, then the same JSON fields.

`fastboot getvar all` (2026-10-27): product blueline, hw-revision MP1.0, bootloader b1c1-0.4-7617406, unlocked,
current slot a, max-download-size 0x10000000; storage MICRON 128GB-UFS-MT rev 9QSN. userdata 0x1BCA7FB000 bytes
(29140987 blocks of 4 KiB), listed last of the LUN 0 group (ssd, misc, keystore, frp, system, product, vendor, boot,
modem, apdp, msadp, klog, metadata, userdata); xbl_a/xbl_b groups follow on other LUNs. If LUN 0 is packed from LBA 6
in that order, userdata would start near LBA 2068232; the GPT read decides.

Storage ABI for 22H384 (2026-10-27): IOBlockStorageDevice keeps its shape (190 vtable slots, 14 pure virtual in both
21H461 and 22H384; `~/blueline/ios18-tools/classvt.py`). `scripts/audit-storage-interface.py` on the Mac's UniXNU
`ipados-18` branch (uncommitted) gained `--static`, `--candidate` and `--original-sha256` (the live GDB observation is
skipped and recorded as `live_observed: false`) and accepts the iOS 18 constructor shape (adrp, add, add #16, str).
Run against kernel 10 with the 22H384 kernelcache hash it passes: 176 kernel bindings, vtable 0xfffffff005cac810,
metaclass 0xfffffff006b891d0, protected constructor 0xfffffff004925e20 (forwards to IOService::IOService), base
destructor 0xfffffff004925e44. Saved as `~/blueline/ios18-tools/storage/interface-audit-22H384.json`.
Next: import fajita's NativeUfs (SDM845 SMMU changes) and VirtioMmioBlock, feed this audit to
`build-virtio-mmio-bindings.py`, and build a read-only GPT test.

### Kernel 11 (2026-10-27): UFS stack, read-only probe

- Driver: UniXNU-sdm845-common `drivers/ShengUfs` (fajita's SDM845 NativeUfs). xnu-11417 build fix: include
  `vm/vm_kern_xnu.h`/`vm/vm_map_xnu.h` when present and use `mach_vm_protect` (vm_map_protect is not exported).
- NativeVirtioBlock bound to the 22H384 IOBlockStorageDevice (`--audit scripts/data/storage-interface-audit-22H384.json`):
  metaclass 0xfffffff006b891d0, constructor 0xfffffff004925e20 in the image.
- NativeUfs always links VerifiedRoot; `scripts/build-verified-root-empty.py` (UniXNU ipados-18) builds its runtime with
  0 policies (roots fail closed), linked through CONFIG_XNU_EXTRA_LD_ARCHIVES.
- Kernel args: `native-ufs-probe=10` (LUN 0 GPT -> userdata -> first block, as a panic) and fajita's SDM845 SMMU
  values. Kernel 11 `48898cb9…4e55`, run 20 image sha256 `057f637c…fc80`.

References from the owner: github.com/erikkunz/pixel3-ios (iOS 16.7.16 iPhone X userland on blueline; its own UFS
root driver passed the same read-only GPT test on this phone and mounts an HFS+ root from userdata; touch is ST FTM5
over bit-banged I2C with LDO19 via RPMh; display is ABL's command-mode panel) and github.com/NWylynko/ios-flame
(Pixel 4: Metal on Turnip through metal2vulkan, WCN3990 Wi-Fi from userland via `wland`; Pixel 3 has Adreno 630 and a
WCN3990 too). Cloned read-only under refs/.

### Run 20 (2026-10-27): read-only UFS root check passed

```
NATIVE_UFS root check
userdata first=1f9000 last=1dc37fa
block0 magic 1fc84f1c c61aece5 ext4magic=9311 ext4blocks=905559918
manifest status=1 windows=0 @NativeUfs.cpp:1840
```

The iPadOS 18 kernel adopted the UFS host ABL left running (SDM845 SMMU stream 0x100 with adoption), read LUN 0's GPT
and found userdata: 0x1BCA7FB blocks of 4 KiB, exactly fastboot's partition-size:userdata. Block 0 is not ext4 (Android's
encrypted userdata) and no UniXNU manifest is present, as expected before flashing. board.toml `[userdata]` now holds
first_lba 0x1f9000, last_lba 0x1dc37fa.

Next: the 22H384 OS images in the IPSW are Apple Encrypted Archives (`094-32804-044.dmg.aea` OS, 5.9 GB;
`094-33089-044.dmg.aea` Cryptex1,SystemOS, 1.6 GB). UniXNU's userdata path has no AEA support (21H461 shipped plain
DMGs), so building the image needs: the download, AEA decryption, the volume UUID, 22H384 root policies for
VerifiedRoot, then `fastboot flash userdata` (replaces Android's userdata only).

### 22H384 OS images and the userdata image (2026-10-27)

- Fetched with `./unixnu ipsw fetch` on the Mac (range requests): OS `094-32804-044.dmg.aea` 5,905,580,032 bytes,
  SystemOS `094-33089-044.dmg.aea` 1,610,612,736 bytes, plus their root hashes and trust caches.
- AEA decryption (UniXNU ipados-18, uncommitted): `scripts/unixnu/aea.py` reads the archive's auth data
  (`com.apple.wkms.fcs-response` = {enc-request, wrapped-key}, `com.apple.wkms.fcs-key-url`), fetches the P-256
  private key from Apple's public key URL (it redirects to fcs-keys-pub-prod.cdn-apple.com), opens the HPKE seal with
  `scripts/aeakey.swift` (CryptoKit, P256_SHA256_AES_GCM_256, empty info) and runs macOS `aea decrypt`. Both archives
  use the same key. `userdata.py` decrypts .aea windows before attaching them. Results: OS 6,845,104,128 bytes,
  SystemOS 3,963,617,280 bytes, both raw APFS (NXSB).
- OS System volume: "CrystalUpdate22H384.J171J172OS", UUID 1E171ABC-554E-4A69-B7A7-35169FAC3565 (board.toml
  root_match_volume_uuid).
- `./unixnu build userdata`: `userdata-root.img` 10,809,769,984 bytes (10.07 GiB) for a 119,361,482,752-byte
  partition; OS at 0x100000 (sha256 7d7dd5c0…4d1a), SystemOS at 0x198100000 (sha256 2d3eee42…6a9c), both 1 MiB aligned.
  Not flashed.
- Root path: NativeUfs publishes a window only after a VerifiedRoot policy of exactly its size verifies
  (configureVerification), and the SystemOS cryptex holds the dyld shared cache, so both windows need policies. A
  manifest carries the full 4 KiB hash table: about 53 MB (OS) + 31 MB (SystemOS) embedded in the kernel.

### Kernel 12 (2026-10-27): root boot, no block verification (owner's choice)

- `build-arm-driver.py` (UniXNU ipados-18) builds NativeUfs with verified blocks unless `XNU_NATIVE_UFS_VERIFIED=0`;
  blueline sets CONFIG_XNU_NATIVE_UFS_VERIFIED=n (no hash tables in the kernel, no integrity check). The empty
  VerifiedRoot archive step and link are gone. NativeUfs fix for that path: the read-path timer helpers were inside
  the NATIVE_VERIFIED_BLOCK block (UniXNU-sdm845-common).
- Trust caches: the unchanged 22H384 StaticTrustCache (2830 entries) and Cryptex1,SystemTrustCache (89) modules,
  bundled by `~/blueline/ios18-tools/trust_bundle.py` (both version 2), sha256 26ed8386…7ce0, pinned in the profile's
  `[payload] trust_cache_sha256`.
- `payload.py` refused a native UFS root without sheng-pmgr=2; it now accepts a board that sets
  `/defaults/vmm-present` (AMFI skips its PMGR latch then).
- Kernel args: `native-ufs-root=1` plus the SMMU values. Kernel 12 `8e77fccb…fb27`, payload `7a4981a8…0b46`
  (root-matching 1E171ABC-554E-4A69-B7A7-35169FAC3565, trust cache, vmm-present), boot image `cdd11895…7879`.
- Userdata image copied to `build/userdata/userdata-root.img` (sha256 d0d975a3…1b2e, matches the Mac's report).
  Not flashed yet.

### Findings from erikkunz/pixel3-ios (refs/pixel3-ios, docs/LOG.md; iOS 16 port on this phone)

- **RAM end.** Its probe read ABL's /memory: one range 0x80000000 + 0xFE0A0000 (4064 MiB, to 0x17E0A0000). Our static
  RAM Partition ran to 0x180000000, 31 MiB past what ABL gives the OS (UEFI allocates top-down from there). Fixed:
  MemoryMapLib 0xB8000000 + 0xC60A0000; DTS memory node and dram_size 0xC6000000 (2 MiB multiple, ends 0x17E000000).
- Fixed carve-outs end by 0xA1C11000 (ramoops/debug_info at 0xA1800000), apart from mainline's Linux-only rmtfs.
- Reboot reason: IMEM 0x146BF65C = 0x77665500 sends the next reset to fastboot; PS_HOLD 0x0C264000.
- ABL may hang receiving `fastboot boot` right after a big flash: Power + Volume Down, then retry.
- XNU's cpu_idle on non-Apple cores cleans the D-cache by set/way on every WFI, which traps to Qualcomm's hypervisor
  (their 0112); 8 cores also hit stale table-walk entries (0117). Both matter before cpus > 1.
- Touch: ST FTM5 at I2C 0x49 on QUP SE2 (0x888000). GPI DMA freezes the SoC (no QUP-DDR interconnect vote); SE2's FIFO
  mode is disabled, so they bit-bang I2C on GPIO 27/28. GPIO 136 high routes the bus to the AP, reset GPIO 99, IRQ GPIO
  125 (TLMM summary INTID 240). AVDD is PM8998 LDO19, owned by the AOP: voted over RPMh (cmd-db at 0x85FE0000, fixed
  128 KiB, apps RSC DRV 2 TCS 0 at 0x179E0D00).
- USB: ABL leaves DWC3 (0xA600000) at SuperSpeed (QMP USB3 PHY); SDM845 GCC USB offsets differ from SM7150; their DMA
  works through the apps SMMU stream 0x740 in an own bank with translation off.
- Display: ABL leaves the command-mode LG SW43408 panel refreshing from 0x9D400000 (1080x2160 a8r8g8b8, stride 4320).
- Userland (iOS 16, for later): data-migration done records, a launchd job deny list (Bluetooth, Wi-Fi daemons,
  routined, syncdefaultsd, keybagd, watchdogd, ...), an AppleKeyStore wrap/unwrap stand-in for securityd, the IOKit
  quiesce hold and IOAcceleratorES polling (their 0107), FBLaunchWatchdogScale, Bold Text hangs SpringBoard without a GPU.
- Sparse images must write zero blocks (their custom img2simg skipped them); stock fastboot uses FILL chunks.

### Repartition plan (owner approved, 2026-10-27): userdata 16 GiB + "ios"

- Secure boot cannot be turned off on a retail Pixel 3 (secure-boot PRODUCTION is eFused); the unlocked ABL is the
  most it allows, and EL2/EL3 protections stay.
- Recovery: official TWRP `twrp-3.7.1_12-0-blueline.img` (67,108,864 bytes, sha256 ae39fe76…d84f, matching
  dl.twrp.me's), header v2, LZ4 arm64 kernel with text_offset 0x80000 (what this ABL requires). Booted with
  `fastboot boot` only; downloads/ is git-ignored.
- `scripts/blueline-gpt-split.py`: parses LUN 0's primary and backup GPT dumps (34 blocks each, CRC-checked), keeps
  userdata's start, ends it after 16 GiB, adds "ios" (Linux data type GUID) on the next 1 MiB boundary up to an
  aligned end, re-parses its output and refuses any other change. On a synthetic copy of the measured layout:
  userdata 0x1f9000..0x5f8fff (16.00 GiB), ios 0x5f9000..0x1dc36ff (95.16 GiB, start byte 0x5f9000000).
- Step 1 (read only): dumps from TWRP (`build/gpt/1-dump-gpt.txt`). Step 2 (after a review and the owner's go):
  write the new blobs with dd, re-read, then `fastboot -w` for Android's 16 GiB and flash the iOS image into ios.

### Run 21-GPT (2026-10-27): LUN 0 repartitioned from TWRP

- Dumps (`build/gpt/lun0-{primary,backup}.bin`, sha256 b914e6ff…8697 / d68af7bb…9117): sda 249675776 sectors
  (31209472 blocks of 4096), 21 entries with no free one (array room for 128 in both copies), userdata last at
  0x1f9000..0x1dc37fa. The split script now grows the array to 22 entries; no partition or header moves.
- New table (`build/gpt/new`, 956ca4c5…0706 / 20a4c21c…e8b9), independently checked: CRCs, entries 0-19 and the disk
  GUID byte-identical; only LBA 1, 2, 0x1dc37fb and 0x1dc37ff change.
- Written with `dd conv=fsync` from TWRP after the pre-write hashes matched the dumps; the read-back hashes match.
  `fastboot getvar`: `partition-size:userdata` 0x400000000 (16 GiB), `partition-size:ios` 0x17CA700000 (95.16 GiB),
  `partition-type:userdata` raw. Userdata has not been formatted (its first 16 GiB are untouched).
- Owner asked to name the partition "darwin": `blueline-gpt-split.py --rename ios=darwin` (now also `--name`,
  default darwin) built `build/gpt/darwin` (729d6f8f…212a / 8ed6419c…f4e8; only entry 21's name and the CRCs differ).
  Steps: `build/gpt/3-rename-gpt.txt`. Written by the owner: read-back hashes match, `partition-size:darwin`
  0x17CA700000, `partition-size:ios` not found, `partition-size:userdata` 0x400000000.

### Kernel 13 (2026-10-27): root from the "darwin" partition

- NativeUfs (UniXNU-sdm845-common `drivers/ShengUfs`): boot argument `native-ufs-partition` names the GPT partition
  holding the root image (default "userdata", exact name match); host test `tests/RootLayout.cpp` covers it (its
  missing ROOT_STAGE/ROOT_LOG/gRootManifestText stubs added). board.toml: `native-ufs-partition=darwin`, `[userdata]`
  0x5f9000..0x1dc36ff. `build/userdata/userdata-root.img` is unchanged (manifest offsets are partition-relative).
- Kernel 13 `7ca962a3…5c1f`; payload `299ec984…3915` (`--trust-cache` 22H384 bundle `26ed8386…7ce0`, dram-size
  0xc6000000 from the RAM fix, ADT `662fe2a5…6a9f`); boot image `build/boot/blueline-unixnu-mu-simplefb-xnu18.img`
  `1ec1c694…00d0` with firmware `d76fc119…` (RAM region to 0x17E000000).

### Run 22 (2026-10-27): root from darwin, "Failed to mount root device"

- `fastboot flash darwin userdata-root.img` aborted on the owner's PC (`std::bad_alloc`, 10 GiB raw image); written
  instead from TWRP with `adb exec-in "dd of=/dev/block/by-name/darwin bs=1M"` (`build/share/4-flash-darwin.txt`).
- Kernel 13 boot image: panic "Failed to mount root device" @IOKitBSDInit.cpp:996, the root matching
  (OS System volume UUID 1E171ABC…, correct for 22H384) never appeared within ROOTDEVICETIMEOUT. NativeUfs's root-mode
  console line and the read-back hash were not reported yet.
- Diagnostic image: same kernel, payload `a8dad994…0338` with `native-ufs-probe=11` (manifest + each window's own GPT
  signature and hash, reported as a panic), boot image `build/boot/blueline-unixnu-mu-simplefb-xnu18-probe11.img`
  `03f2f431…4426`, shared as `blueline-probe11.7z` (`25b3cbd7…b5b1`).
- Probe 11 result: `userdata first=5f9000 last=1dc36ff manifest status=0 windows=2` (the darwin partition, found by
  name); `w0 OS off=100000 size=198000000`, `w1 SystemOS off=198100000 size=ec400000`, both `read=1`, recorded hashes
  7d7dd5c0… / 2d3eee42… (as `userdata-root.json`). `gpt=00000000 00000000` for both: the 22H384 OS and SystemOS
  images are bare APFS containers (`NXSB` at byte 32 of each window, no GPT), unlike the 21H461 images sheng/dipper use
  (GPT at sector 1). So the image on darwin is in place; whether IOKit publishes an APFS container from a whole-disk
  window without a partition map is the open question. Next diagnostic: `native-ufs-probe=12` (real root publication,
  then every IOMedia with BSD name, Content, size and UUID, as a panic).
- Probe 12 image: payload `541a7567…12b0` (`native-ufs-probe=12`, same kernel 13 and trust cache), boot image
  `build/boot/blueline-unixnu-mu-simplefb-xnu18-probe12.img` `b9a1fae7…c5f9`, shared as `blueline-probe12.7z`
  (`7e388cf7…15be`).

### Run 23 (2026-10-27): probe 12 root-start failure; kernel 14 follow-up

The owner reported `root start failed (blocks=0) @NativeUfs.cpp:1905` from the kernel 13 probe 12 image
(`b9a1fae7…c5f9`). The last confirmed storage stage remains run 22's probe 11: darwin found by name,
manifest accepted, both APFS windows read. No root block device was published in this probe 12 run.

The interrupted conversation fixed `drivers/ShengUfs/NativeUfs.cpp` in UniXNU-sdm845-common:
`startRoot`'s identity validation returned false whenever verified blocks were disabled, despite the owner's
`CONFIG_XNU_NATIVE_UFS_VERIFIED=n` choice. Kernel 14 permits that build to dispatch the publisher. Without a
signed UUID, it waits for a UUID-bearing IOMedia whose parent traversal reaches the actual window, records the
observed UUID, and keeps the SystemOS-first/OS-last publication order. Verified builds retain signed UUID
validation. This does not establish whether bare APFS windows publish volume media on 22H384.

Build identity (working-tree changes remain uncommitted):

- UniXNU Mac branch `ipados-18`, base `6c6aab3f0f54b7ff01dfab15f75791194aac3b2e`; device base
  `2470d1acea847b480df66c5b914a2e28f0442151`; SoC base `d2a5f3b7907af94d2f6652f212285d7a9caacde6`;
  firmware base `c092b9712c3f02d1889026812b6597a0b8eeaea4` plus the documented RAM fixes.
- NativeUfs.cpp MD5 `70dfbba34b91767ee89a82bd02a8b1b6`, SHA-256
  `a1b0618cad1e5e27bf641fd330ddc500bf22c9913b33f469efed36efbea1a23e`; local and Mac copies match.
- Kernel 14: `~/blueline/UniXNU/out/blueline/ipados-18.7.11-22H384/blueline/kernel/kernel.macho`,
  55,373,824 bytes, SHA-256 `8abca4165ab3978ef0618a896d451bc5707fb0562b7b8414e6239e6ee7e68045`.
  Copied to `build/kernel/ipados-18/kernel-k14.macho`.
- Firmware reused: `build/firmware/blueline-mu-simplefb-debug.fd`, SHA-256
  `d76fc1199f2b60e1cad5da1c72971ad07f513b8a03d7cc31f19968cd50d3ca1b`.

The Mac commands were run from `~/blueline/UniXNU` after sourcing `~/blueline/env.sh`:

```sh
./unixnu build kernel > ~/blueline/kernel14.log 2>&1
./unixnu build payload --kernel out/blueline/ipados-18.7.11-22H384/blueline/kernel/kernel.macho \
  --uefi ~/blueline/uefi-font --trust-cache ~/blueline/ios18-tools/trust/22H384-static-system.bundle
./unixnu build payload --kernel out/blueline/ipados-18.7.11-22H384/blueline/kernel/kernel.macho \
  --uefi ~/blueline/uefi-font --trust-cache ~/blueline/ios18-tools/trust/22H384-static-system.bundle \
  --kernel-arg native-ufs-probe=12
```

Kernel build exit 0 and both payload builds were recovered from the interrupted session's logs. Local logs:
`build/resume-k14/{kernel14,payload14-normal,payload14-probe12}.log`. Each payload was saved locally before the
next build overwrote the Mac's `payload.img`; its current copy on the Mac is probe 12.

| Artifact | Local output | SHA-256 |
| --- | --- | --- |
| Normal payload | `build/payload/ipados-18/payload-k14-normal.img` | `89c80a8c1726355337e9696615f1a54a31819a9abbdcb45d9824204b83b43ae8` |
| Probe 12 payload | `build/payload/ipados-18/payload-k14-probe12.img` | `1a51edaf1919f6b235dbb3f32e3267944630a2eb8b46670cc56f75e0089c48cf` |
| Normal boot | `build/boot/blueline-unixnu-mu-simplefb-xnu18.img` | `26425c940464357efd864f07cd4ab07052ce3b933aebc3de2782ecdc31f2cc6f` |
| Probe 12 boot | `build/boot/blueline-unixnu-mu-simplefb-xnu18-probe12.img` | `18d2439eec7cdc8bb4d0a7f638dfccb1c496e174ce625408d4ad7b0b118fd4e0` |

Packaging on Windows:

```powershell
python scripts/package-blueline-boot.py --fd build/firmware/blueline-mu-simplefb-debug.fd --ramdisk build/payload/ipados-18/payload-k14-normal.img -o build/boot/blueline-unixnu-mu-simplefb-xnu18.img
python scripts/package-blueline-boot.py --fd build/firmware/blueline-mu-simplefb-debug.fd --ramdisk build/payload/ipados-18/payload-k14-probe12.img -o build/boot/blueline-unixnu-mu-simplefb-xnu18-probe12.img
```

Both images are 84,951,040 bytes: header v1, text_offset 0x80000, 80 MiB payload. The packager's header,
gzip/firmware, DTB and ramdisk round-trip checks pass. Independent FAT extraction and boot-ramdisk checks
(`build/resume-k14/verify-artifacts.py`, report `artifact-verification.json`) confirm the patched kernel,
ADT and trust-cache hashes and the actual command lines. Normal has no probe argument; the diagnostic adds
only `native-ufs-probe=12`. Both use darwin, root UUID `1E171ABC-554E-4A69-B7A7-35169FAC3565`,
dram-base `0xb8000000`, dram-size `0xc6000000`, a 2 GiB XNU window, and vmm-present=1. Trust cache SHA-256 is
`26ed8386ab1984442d2e65679bc4ed4544ba8a71dcfccdecaf39e5ceb6ef7ce0`. Neither includes a Data seed.

Existing host checks pass on the Mac (RootLayout, RootWindowRead, RootDiagnostics, RootPublication), including
the existing root image's manifest, lifecycle rollback, publication order and bounded ancestry. Command:
`python3.12 UniXNU-sdm845-common/drivers/ShengUfs/tests/check_root_path_host.py --output ios18-tools/storage/k14-host --openssl-prefix /opt/local/libexec/openssl3 --root-image UniXNU/out/blueline/ipados-18.7.11-22H384/blueline/userdata/userdata-root.img`
from `~/blueline`, using `/usr/bin/clang++` and the macOS SDK. The first attempt inherited the kernel compiler
environment and failed to find `sys/types.h`; setting SDKROOT to `xcrun --sdk macosx --show-sdk-path` and
PATH to `/usr/bin:/bin:/opt/local/bin` resolved it. Logs: `build/resume-k14/k14-host-check.log` and
`~/blueline/ios18-tools/storage/k14-host/`. These checks do not exercise IOKit's new media lookup on hardware.

The test bundle is `build/share/blueline-run23-kernel14.7z`, with both boot images, JSON reports,
`SHA256SUMS.txt` and `TEST-KERNEL14.txt`; standalone steps are `build/share/5-test-kernel14.txt`.
It is 7,855,153 bytes, SHA-256 `1195b21fb8dce7e85afb4aa42633ae29e6c2bd7713b4646d391ddcca89d15364`.
`7z a <bundle> build/resume-k14/share-stage/* -mx=9 -mmt=8 -ms=on` and `7z t <bundle>` succeed.
The running share's local and external download routes return that exact archive hash.
The previous kernel 13 boot images and reports are retained under `build/boot/history/kernel13-run23/`.
Older `blueline-probe12.7z` still contains kernel 13.
Local canonical `build/kernel/ipados-18/kernel.macho` and `build/payload/ipados-18/payload.{img,json}`
now select kernel 14 and its normal payload; named variants remain available. The Mac's canonical payload is probe 12.
`./unixnu status --json` exits 0 on the Mac (`build/resume-k14/status-k14.json`): the SDM845 SoC supplies
ShengUfs and ArmCpu, the ipados-18 profile supplies the matching ABI archive recipes, and the resolved partition
and root arguments match this board. Board preflight and porting-record validation pass (13 structured areas).

Next phone test: boot probe 12 first and photograph its deliberate IOMedia panic (published count,
reads/failures, BSD names, Content, size, UUID); then boot normal and capture the last screen. Use the
already-written darwin image; this follow-up requires no root-image rebuild or flash.

Hardware results remain separate: XNU previously reaches BSD/root matching; UFS block reads are confirmed,
but kernel 14 publication/root mount and userland are untested. Firmware display and keys work; kernel touch,
kernel buttons, accelerated GPU rendering and persistent Data remain unverified or unimplemented.

### Run 24 (2026-10-27): owner reports post-mount root authentication failure

Owner report: `rootvp not authenticated after mounting`. The exact image variant, full panic and screen were not
supplied. This is the latest reported stage; do not assign it to a specific image hash. The owner requested the
existing Sheng/Pocket Max fix, a proactive startup/signature argument audit, and eight CPUs with the clock settings
from the Pixel 3 iOS 16 port mentioned to Claude.

The existing unverified UFS publication choice does not authorize the mounted root through Apple's
FSIOC_KERNEL_ROOTAUTH. Pocket Max had the same sequence: root authentication, missing SystemOS shared cache,
ignition/Preboot lookup, then refusal of 16 KiB CodeDirectory pages on its 4 KiB kernel.

### Prepared run 25 (2026-10-27): kernel 17, root/cache fixes and eight-core candidate

**Build completed; no phone run of this candidate yet.**
[BOOT-ARGS-AUDIT.md](BOOT-ARGS-AUDIT.md) records the full comparison, selected settings, provenance and limits.

- Recovered UniXNU's existing Pocket Max `zzy-debug-rootauth-bypass.patch` and checker from
  `bc5337b8d838267fb17a74b2314a436cf2199e53`. Apple root authentication still runs; the exact
  `debug-rootauth-bypass=1` argument plus `CONFIG_XNU_DEBUG_ROOTAUTH_BYPASS=y` logs its failure and permits
  this owner's unverified root. Default build switch is 0; the normal owner path remains.
- Recovered `zzy-debug-systemos-union.patch` from `2dc5b8dedf9a69c39d1a41f1aa9438dc1a8c5c21`, adapted the
  vm_unix context for XNU 11417 and made its UUID use `XNU_OWNER_CACHE_VOLUME_UUID`. The build defaults off,
  requires the root-auth switch and rejects the authenticated owner mount at the same time.
  NativeUfs adds a bounded BSD-device lookup requiring exactly one UUID match below its attached SystemOS window.
  That callback supplies a device path, not authentication. SystemOS remains read-only.
- Mounted the original decrypted 22H384 OS/SystemOS images read-only on the Mac, then detached them. Measured OS
  UUID `1E171ABC-554E-4A69-B7A7-35169FAC3565` and SystemOS UUID `3C5B8DDB-FEED-44F1-852D-F5F64FAB3EBB`.
  OS has launchd/dyld and no shared-cache directory; SystemOS has the cache. The device sets the measured UUID,
  `debug-systemos-union=2 ignition_level=1`, following Pocket Max's recorded path to cache registration.
- Explicitly set `CONFIG_XNU_NATIVE_CS_HASH_CACHE=y` (already inherited from common.config). The OS18 profile
  already rebases the native 16 KiB-to-4 KiB signature patch. All 62 measured SystemOS cache CodeDirectories have
  SHA-256/page shift 14, and all CDHashes occur in the actual payload's original Apple trust bundle (2830+89 entries).
  Code signatures/AMFI remain active. Foreign hardware args, the PIO SD unsigned-cache shortcut and unqualified
  OS17 text-fixup offsets are not copied. Data/Preboot inputs remain separate unresolved work.
- Inspected erikkunz/pixel3-ios `5849d065053d76759e28202c12eb17eb2f589d28`, patches 0101/0106/0112/0117
  and LOG.md. Enable affinities `0x000..0x700`, `cpus=8 sheng-smp=1`, self-SGIs and timer INTID 19. The existing
  SoC secondary patch supplies eight slots/Affinity1 reset matching. Omit E/P ADT tags: this native BCM2837
  personality has one SMP scheduling cluster, as the iOS 16 port does.
- Adapted 0106 into the SoC's ArmCpu: `bluecpufreq=1` requests the highest regular firmware LUT level, skips
  one-core turbo and honors per-core DCVS. Stock Pixel 3 DTB's cpucc confirms domains `0x17d43000+0x1400` and
  `0x17d45800+0x1400`. Translate ADT arm-io bus offsets before requiring those physical windows. Requests run
  when a core starts, so Gold can retry after CPU_ON if its controller was initially disabled. Disabled/unusable
  controllers receive no writes; firmware retains voltage/thermal control. Nominal regular clocks from pixel3-ios
  are Silver 1.7664 GHz and Gold 2.6496 GHz, exported in the ADT; actual speed remains unmeasured on this phone.
- SoC series adapts 0112 to omit the whole-cache set/way flush during coherent WFI, and 0117 to recover repeated
  successful translation/write faults with VA-wide TLBI after 10,000 repetitions. `sdm845-fault-loop=1` is explicit,
  development-only and leaves vm_fault's result unchanged. This is a bring-up workaround, not a general teardown fix.

Reusable kernel patches, their default-off build integration and profile selection are in the Mac's UniXNU
checkout, mirrored locally under `refs/UniXNU-main/`. SDM845 operations/resources and tests are in
`C:/Users/user/UniXNU-sdm845-common`; board selection/nominal clocks/arguments are in this device repository.
No component IDs, runtime names or source pins were changed. Board preflight now recognizes `sheng-smp` as the
generic ArmCpu gate; it still refuses `sheng-cpufreq` and the foreign hardware policies.

Intermediate kernel 15 (root-auth only) built with SHA-256
`1f4ff43b3657028bd76d79758cf61a81cc04230b3619b225a810579dd0c08f38`.
Kernel 16 (root-auth/SystemOS/signatures, one CPU) built with
`bc1f3784bec90e4ba5de8d3c088dc05025d37092bcf85c123679cd59739369dd`; its packaged image is retained under
`build/boot/history/kernel16-root-dyld/`. Neither intermediate was shared for a phone run.

Final source state: device base `2470d1acea847b480df66c5b914a2e28f0442151`, SoC base
`d2a5f3b7907af94d2f6652f212285d7a9caacde6`, firmware base `c092b9712c3f02d1889026812b6597a0b8eeaea4`,
Mac UniXNU `ipados-18` base `6c6aab3f0f54b7ff01dfab15f75791194aac3b2e`, with previous work plus this turn's
uncommitted changes. The Mac's device/SoC are source copies without Git metadata. No commits, pushes or publishing.
NativeUfs.cpp SHA-256 `081bb24a7f7d6d4e61cd0f7eb29fb00a6f3353e757559de09b7d751bebeb4db1`;
ArmCpu.cpp `f0572f7d6f5fcbba97e541984b95751dd5c75d39ddf4b8c8160097a5dcd78f1a`; local/Mac copies match.

Commands, from `~/blueline/UniXNU` after sourcing the owner's env.sh:

```sh
./unixnu defconfig blueline_defconfig -D ../UniXNU-blueline
./unixnu status --json
# Safely removed only generated tree/build/xnu and tree/.cache/xnu-work under the resolved device output.
./unixnu build kernel
./unixnu build payload --kernel out/blueline/ipados-18.7.11-22H384/blueline/kernel/kernel.macho \
  --uefi ~/blueline/uefi-font --trust-cache ~/blueline/ios18-tools/trust/22H384-static-system.bundle
# Save named eight-core payload before building the comparison; extra named args replace board values.
./unixnu build payload --kernel out/blueline/ipados-18.7.11-22H384/blueline/kernel/kernel.macho \
  --uefi ~/blueline/uefi-font --trust-cache ~/blueline/ios18-tools/trust/22H384-static-system.bundle \
  --kernel-arg cpus=1 --kernel-arg sheng-smp=0
# Restore the named eight-core payload as the Mac's canonical payload.img/json.
```

Windows packaged each named payload using `python scripts/package-blueline-boot.py --fd
build/firmware/blueline-mu-simplefb-debug.fd --ramdisk <named payload> -o <image>`.
Scripts/logs/reports are under `build/resume-k15/`, `build/resume-k16/`, `build/resume-k17/` and the matching Mac logs.

| Output | SHA-256 |
| --- | --- |
| `build/kernel/ipados-18/kernel-k17.macho` | `423ec845a43677df710f66396a36679d213aebc93c50e8a9ec3f1b0cd0fabd35` |
| `build/payload/ipados-18/payload-k17-eightcpu.img` | `054d40780de1613a2396edd60b889420adbc68557cd754aab28907c3f5b72d76` |
| `build/payload/ipados-18/payload-k17-onecpu.img` | `418d95fe6d676c36c85fe2bcf961a0e6e92072915df67abb6184ff93b56715d1` |
| `build/boot/blueline-unixnu-mu-simplefb-xnu18.img` (eight cores) | `235d52916e5a7cf8616118131524a67724506babdc71d5bf2d8b9887a425cf56` |
| `build/boot/blueline-unixnu-mu-simplefb-xnu18-onecpu.img` | `497a25cde236e98e35ddaa9613198279914bcc78908089446a75790afdb98416` |

Both boot images are 84,951,040 bytes, header v1/text_offset 0x80000, 80 MiB payload, unchanged root image,
2 GiB XNU window, vmm-present=1, no Data seed/GPU addon. Firmware reused:
`d76fc1199f2b60e1cad5da1c72971ad07f513b8a03d7cc31f19968cd50d3ca1b`. Original 22H384 trust bundle:
`26ed8386ab1984442d2e65679bc4ed4544ba8a71dcfccdecaf39e5ceb6ef7ce0`.

Verification: 126 compiled root-auth cases pass (default-off, argument values, Apple ioctl/owner path and build
integration). Eight compiled actual-source signature gate cases pass on the prepared XNU 11417 source; they test
the page-size gate, not the full hardware validator. Final kernel/compiler checks confirm root/SystemOS flags,
measured UUID, native signature verifier/version/counters and CPU frequency/fault-loop symbols. Existing UFS
RootLayout/RootWindowRead/RootDiagnostics/RootPublication checks pass with ASan/UBSan; the actual CPU frequency
planner's disabled/no-regular/turbo/40-row/termination/per-core/no-write cases pass with ASan/UBSan. Use host
SDKROOT from `xcrun --sdk macosx --show-sdk-path` and PATH `/usr/bin:/bin:/opt/local/bin` for these probes.
Independent 7-Zip FAT/ADT/embedded-ramdisk checks pass for both variants, including eight affinities, nominal
clocks, physical OSM windows and all cache trust entries. Board preflight and 13-area porting-record check pass.

Delivery: `build/share/blueline-run25-kernel17.7z` contains both images, JSON reports, the startup/CPU audit,
SHA256SUMS and TEST-KERNEL17.txt. Size 7,863,338 bytes, SHA-256
`33598ef8a24c502dc2191d518c03bd1c3a6597faee06bb49b6064f931892fd02`.
`7z a <bundle> build/resume-k17/share-stage/* -mx=9 -mmt=8 -ms=on` and `7z t <bundle>` pass;
both local/external downloads match the full archive hash. Download results are recorded in HANDOFF.md and
`build/resume-k17/download-verification.json`. Previous kernel 14 normal image is retained under
`build/boot/history/kernel14-run24/`; probe 12 still contains kernel 14. Canonical local/Mac kernel/payload select
kernel 17 eight-core. The same-kernel one-core payload overrides only `cpus=1 sheng-smp=0`.

Next phone test: temporary `fastboot boot blueline-unixnu-mu-simplefb-xnu18.img`. Photograph the full last screen,
timing, CPUFREQ/root-auth/SystemOS/cache messages. If it stalls, compare
`fastboot boot blueline-unixnu-mu-simplefb-xnu18-onecpu.img`. The existing darwin image needs no reflash.
Hardware results remain separate: root authentication is the latest owner report; new SystemOS mount, dyld
registration, eight active CPUs and actual clocks are pending. Firmware display/keys work; kernel touch/buttons,
GPU and persistent Data are still unimplemented or unverified.

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

## Target OS: iPadOS 18.7.11 (2026-10-27)

The owner moved the target OS to `iPad_10.2_18.7.11_22H384_Restore.ipsw` (iPad 7th generation, iPad7,11 J171AP /
iPad7,12 J172AP, chip 0x8010, still signed), the last iPadOS build for an arm64 (not arm64e) device. Its kernelcache:
`xnu-11417.140.69.706.66~1/RELEASE_ARM64_T8010`, Darwin 24.6.0, cpusubtype 0, classic prelinked layout, 229 kexts,
decompressed sha256 `adc2b3e3d2b0a87e6ebb71f5f5c2dbd183872a5e3c2a4636e331a0cf1f5df5e9`.

Upstream UniXNU has only `ipados-17.7.11-21H461` (xnu-10063.141.1). The new OS profile `ipados-18.7.11-22H384` lives on
branch `ipados-18` of the build Mac's UniXNU checkout (`~/blueline/UniXNU`); the rebase tools and a bundle of the
rebased xnu branch are in `~/blueline/ios18-tools`.

- Source: public `xnu-11417.140.69` (the base of the shipped kernel). Apple removed the BCM2837 machine layer after
  10063, and its public tree has only Apple Silicon Mac machine configs. `restore-bcm2837-machine.patch` brings it back
  (MASTER.arm64.bcm2837 with the 11417 scheduler options, BCM2837.h, board_config.h, MakeInc.def, the `#if BCM2837`
  paths; not the Pi 3 mini-UART, as 11417's serial layer is devicetree driven).
- The 61 patches of the arm64, SoC and OS series, replayed on 10063 and cherry-picked: 51 apply unchanged (both SoC
  patches among them), 8 were rebased (kernel-sdk-root, disabled-deferred-reclaim, native-4k-signature-hash-cache,
  native-text-fixup, non-apple-cache-monitor, offline-owner-systemos-mount, zzz-boot-progress, plus the restore), 2 are
  no longer needed (non-apple-pmap-field, published-freezer-condition: fixed by Apple).
- Prelink: all 31 roots exist; the AMFI early security initializer `_initializeAppleMobileFileIntegrity()` is at
  `0xfffffff005a79dc8` (found from its `__PRETTY_FUNCTION__` string, the same function as 21H461's address).
- New in 11417: `arm_init()` panics unless `/chosen` has `dram-base` and `dram-size`.
- Build fixes past the rebase (all Apple's own `!CONFIG_DEFERRED_RECLAIM` stubs, a header that common code now
  includes, `vm_map_protect` gone private, and UniXNU's command-line `vm_unix.o_CFLAGS_ADD` replacing 11417's
  per-file `-DVM_UNSAFE_TYPES`): listed in the profile's README.

### Kernel 2 (2026-10-27): iPadOS 18.7.11, Intel build Mac

UniXNU branch `ipados-18` at `6c6aab3` (on upstream `f85b5a6`), UniXNU-blueline with board.toml on the new profile,
UniXNU-sdm845-common `d2a5f3b7`; same Mac and toolchain as kernel 1. Sixth attempt; the build itself takes about
2.5 minutes.

- `kernel.macho` 55291904 bytes, sha256 `bf8e6436533305c563951317fd2d0080e880dfbd136752934e69266b3bfe5026`, Darwin 24.6.0
  DEVELOPMENT_ARM64_BCM2837, prelinked on the 22H384 kernelcache. Copied to `build/kernel/ipados-18/` (ignored).
- Prelink report: the same class as kernel 1 (`loadable=false`, `runtime_qualified=false`); 30 roots with code and
  IOHIDEventDriver codeless, AMFI early initializer recorded; ArmCpu from the SoC repository (`armcpu-timer-intid`
  is in the image).
- Not run on the phone: besides the DRAM size, the payload needs this profile's text fixups, trust caches and OS volume
  UUID, and the boot path must give `/chosen` `dram-base`/`dram-size`.
- Not done: payload text fixups and trust cache pins for 22H384, the 22H384 OS volume UUID
  (`[payload.adt] root_match_volume_uuid`), the J171 `product-id`.

## First XNU test image (2026-10-27): `blueline-unixnu-mu-simplefb-xnu18.img`

- DRAM: `dts/blueline.dts` memory node and `[payload] dram_size` are 4 GiB from 0x80000000 (every retail Pixel 3;
  dipper and fajita use the same SDM845 convention). XNU only gets DarwinBoot's window, one contiguous run of the UEFI
  memory map: `owned_memory_size` was 1 GiB for run 6 and is 2 GiB from run 7 (run 6 explains the firmware memory fix).
- `panic_screen = true`: the entry probe bars and panic() drawn on screen, since the phone has no serial port. The
  probe tool (`scripts/patch-xnu-entry-probe.py` of UniXNU) stopped its panic-screen space 16 bytes before the page
  end; kernel 2's panic screen (0xa37 bytes) needed 7 of them. On the Mac's `ipados-18` branch the space now runs to
  the end of the zero padding (verified zero up to 0xfffffff007472000). Not committed there yet.
- Payload (`./unixnu build payload --kernel <kernel 2> --uefi <dir with Mu-Silicium's Font.h>`): 80 MiB, sha256
  `debfc7ce…c746`; patched kernel `57fdf8e7…5720`, ADT `755366ad…a13b` (dram-base 0x80000000, dram-size 0x100000000,
  darwinboot-memory-size 0x40000000). Entry `xnu` boots after 3 s; no trust cache, no data ramdisk, no root UUID.
- Boot image: `scripts/package-blueline-boot.py --fd build/firmware/blueline-mu-simplefb-debug.fd --ramdisk <payload>`,
  header v1, 84951040 bytes, sha256 `fac59ac7…186d` (the run-4 firmware with an 80 MiB ramdisk instead of 16 MiB).
- Run 7 image (after run 6): firmware with the memory fix (`blueline-mu-simplefb-debug.fd` `c7f3e644…da8a`), payload with
  the 2 GiB window (`44704636…6d29`), boot image sha256 `79dfa188…639b`, same name and size.
- Run 8 image (after run 7): firmware with one static region 0xB8000000 + 0xC8000000 and the initrd reservation
  (`cd5d6ef9…dee1`), same payload, boot image sha256 `e6df9dd9…032f`, same name and size.

What to look for, in order:

| Screen | Meaning |
| --- | --- |
| fastboot error on `boot` | the 85 MB image or its 80 MiB ramdisk is refused: send `fastboot getvar max-download-size` |
| UniXNU menu, then `DARWINBOOT: ... NO WINDOW ... BIG RUN ...` | the 1 GiB window does not fit; the line gives the largest free run |
| green band at the top only | DarwinBoot handed off; XNU did not reach its entry |
| green + blue band | XNU entry ran (stage 1) |
| green + blue + magenta | XNU got to just before enabling the MMU (stage 2) |
| text on the screen | the verbose console or a panic message: photograph it |
| "waiting for root device" / stall after IOKit | expected end: there is no UFS driver yet |

## Open items

- DRAM: 4 GiB set (nominal); the largest free run for the DarwinBoot window is not measured. The userdata LBA range: read on the phone.
- Root device: an XNU driver for the SDM845 UFS host (UFS 2.1, 0x1d84000); NativeUfs is SM8550-specific.
- Kernel build: macOS (Xcode) or a GitHub macOS runner, as `UniXNU-pocket-max/.github/workflows/build-kernel.yml`.
- Keys in `mu-simplefb`: done (BluelineKeyInputDxe, run 4).

### Run 25 result (2026-10-27): both kernel 17 variants reach AMFI at child execution

The owner initially identified the main eight-core image and then confirmed the
one-core image has the same panic. The supplied photo reads:
`AMFI: failed to create OSDictionary copy: 14` at `OSEntitlements.cpp:827`,
pages `0x79c53`, free `0x48c58`. Photo: [run25-amfi.jpg](images/run25-amfi.jpg).

Mapping against the kernel 17 symbols: `cs_entitlements_dictionary_copy_vnode`
→ `IOUserClient::copyClientEntitlementVnode` → `IOVnodeHasEntitlement`
→ `exec_mach_imgact` → `exec_activate_image` → `posix_spawn` → syscall exception.
The checks concern hardened-process flags during child execution. This establishes
userspace execution beyond the previous root-auth wall; it does not identify the
parent/child binary or show the earlier mount lines. The owner comparison separates
this panic from eight-core startup. Active core count and actual clock remain pending.
Kernel 17 had no Data seed or writable `/private/var` mount.

### Prepared run 26 (2026-10-27): kernel 18, per-key AMFI query and persistent Data

The original 22H384 AMFI callback `copyEntitlementAsOSObject` selects the named key
before conversion. The OS profile now uses it for single-key vnode queries, matching
Apple's task-based query path, with the same cached signed blob and retained result.
No broad AMFI policy/signature bypass is added. Other full-dictionary consumers
remain a hardware observation item. Optional diagnostics identify the executable,
key, result and CDHash prefix. Detailed inputs: [PERSISTENT-DATA.md](PERSISTENT-DATA.md).

RamBlock revision `25c57e0e9b68bf452dd1986914525edc9d2320bc` supplies the large packed
seed and persistent overlay. The new separate development Data gate keeps the
authenticated owner requirements unchanged and refuses a volatile/failed store.
Data: UUID `789552B2-C2BA-476F-BE80-99D2ECA02A7C`, 80 GiB logical, 1093760 bytes packed,
267 nonzero pages; encoded SHA256
`e3b1ea02c0baea7e1c085b6734178b23966e998c26b256cb1423416a5d1835fd`, indexed seed identity
`023d33e855bd5c9894d5e7cbd5247ea1bb9b88797137245648e4602877523c57`.
Store: `0x98347f..0x1d836ff`, 4096-byte blocks, 20972161 blocks including header/bitmap,
1 GiB reserve. Original OS/SystemOS root image and Android userdata remain outside it.
The seed preserves 41 initial var files from read-only 22H384 inspection and adds
empty upstream fixture directories; no keys or activation/user preferences were added.

Reproduction on Mac (source `~/blueline/env.sh`):

```sh
python3 ../UniXNU-blueline/scripts/prepare-blueline-data.py --unixnu "$PWD" \
  --output ~/blueline/ios18-tools/blueline-data --gib 80
./unixnu defconfig blueline_defconfig -D ../UniXNU-blueline
./unixnu status --json
./unixnu build kernel
./unixnu build payload --kernel out/blueline/ipados-18.7.11-22H384/blueline/kernel/kernel.macho \
  --uefi ~/blueline/uefi-font --trust-cache ~/blueline/ios18-tools/trust/22H384-static-system.bundle \
  --data-ramdisk ~/blueline/ios18-tools/blueline-data/blueline-data.dbr
```

Seed creation refuses to overwrite its folder; reuse the existing seed on subsequent
builds. The second payload adds `--kernel-arg cpus=1 --kernel-arg sheng-smp=0`; named
normal files are restored to canonical outputs afterward. Windows packaging:

```sh
python scripts/package-blueline-boot.py --fd build/firmware/blueline-mu-simplefb-debug.fd \
  --ramdisk build/payload/ipados-18/payload-k18-eightcpu.img -o build/boot/blueline-unixnu-mu-simplefb-xnu18.img
python scripts/package-blueline-boot.py --fd build/firmware/blueline-mu-simplefb-debug.fd \
  --ramdisk build/payload/ipados-18/payload-k18-onecpu.img -o build/boot/blueline-unixnu-mu-simplefb-xnu18-onecpu.img
```

Initial Data patch context was rebased onto the OS series after boot progress; all
patches apply. A diagnostic probe was corrected for OSRuntime's raw-pointer API.
Bootstrap now requires both the startup compile record and link.filelist. RAM binding
proofs verify NativeRamBlock.o (rather than nonexistent RamBlock.o). An unnecessary
interface-version guard was removed after original AMFI inspection; final build is
the third successful intermediate kernel 18 build, with the exact hash below.
These fixes are recorded in the OS profile/build scripts, not board hardware patches.

Host validation passes: 11 actual vnode query cases; 16 actual Data mount cases under
ASan/UBSan; upstream overlay/direct disk restart, lost-cache/torn-write/flush failures,
packed base import/hash, actual 80 GiB indexed identity, compact seed and sparse
4000-operation differential tests. RamBlock's stale 2 GiB-limit malformed test was
updated to its current 1 TiB format limit; runtime source is unchanged. 22H384 storage
bindings verify 190 virtual methods, 24 overrides and zero unresolved imports.
Final compiler definitions/symbols, payload FAT/ADT/packed seed/boot ramdisk and all
62 original SystemOS CDHashes in the actual trust bundle pass for both variants.

Artifacts (ignored):

| Artifact | SHA256 |
| --- | --- |
| `build/kernel/ipados-18/kernel-k18.macho` | `ad622a97dd41b69463fec44ccd70e3b5685128e317b2b74a30a9350fb0d00198` |
| `build/payload/ipados-18/payload-k18-eightcpu.img` | `a6532c39ade16c7833fa13c180eb38e907ac797d0c7f1f011c4a9e69713f44b6` |
| `build/payload/ipados-18/payload-k18-onecpu.img` | `1547d78106d47c56437fb01fd371e935718a43cfbcf1fc64aa5e30dbfc75483a` |
| `build/boot/blueline-unixnu-mu-simplefb-xnu18.img` | `1007eae780bbb5e3172033e17117be8f152ee3905589b378eefc7892815ba414` |
| `build/boot/blueline-unixnu-mu-simplefb-xnu18-onecpu.img` | `b44c5977bd604a7e356202520fe30484e2daae565ee2518ca8e13d8d8d218642` |

Both boot images are 84951040 bytes with 80 MiB payloads, header v1/text_offset
0x80000 and the existing firmware d76fc119...d3ca1b. Kernel 17 images are preserved
under build/boot/history/kernel17-run25. Root image unchanged; no reflash needed.
Sources remain uncommitted; device/SoC/main/firmware bases are the run 25 revisions.

Next hardware test: temporary boot the eight-core image, capture Data state and
mount status/readonly, AMFI DIAG key lines and the full last screen. Reboot the same
image to observe resumed state 2. Actual file survival, complete userland, active
CPU count/clock, kernel touch/buttons and GPU remain unverified. The persistent
arguments do not reset Data; preserve the seed identity and geometry.

Run 26 delivery: `build/share/blueline-run26-kernel18.7z`, 8429174 bytes, SHA256
`18552bf72ea3c988d58012a2b4e460c3704b2811f9cee566805aea88c9fc45cd`. Includes both boot images, stable seed, metadata, verification reports,
source/input notes and TEST-KERNEL18.txt. `7z a -mx=9 -mmt=8 -ms=on` and `7z t` PASS.
The complete archive downloads through localhost and the existing private external
share both match that SHA256. Board preflight and all 13 porting-record areas pass;
hardware verification levels remain unchanged.


### Run 26 result (2026-10-27): kernel 18 reaches AMFI's signature check, status 14 again

Owner photo (kernel 18): `AMFI: failed to create OSDictionary copy: 14` @`OSEntitlements.cpp:827`, pages 0x79b39,
free 0x41d8a. Frames mapped against kernel 18 (`ad622a97…`, kernel frames slide 0, AMFI frames at kernelcache
address minus 0x1000000): AMFI 0x…5a8fecc (panic helper, line 0x33b) ← 0x…5a803d4 (calls the converter
0x…5a860c8, status ≠ 0 → panic) ← 0x…5a80338 ← 0x…5a7b090 ← `mac_vnode_check_signature` ← `ubc_cs_blob_add`
← `load_code_signature` ← `parse_machfile` ← `load_machfile` ← `exec_mach_imgact` ← `exec_activate_image`
← `posix_spawn` ← `unix_syscall`. The kernel 18 per-key query fixed the run 25 caller (`IOVnodeHasEntitlement`);
this is AMFI's own full-dictionary conversion at code-signature registration, run for every signed exec.
Status 14 is returned by 0x…5a8536c when its recursive DER-to-OSObject builder (0x…5a85434) returns NULL: a
factory returned NULL or a DER element type outside 1-6. The factories (table 0xfffffff006cf86b0) bind to the
right native functions (OSString::withCString(const char*, size_t), OSSymbol::withString,
OSDictionary/OSArray::withCapacity(0), OSNumber::withNumber(…, 64), OSData::withBytes, OSBoolean::withBoolean);
a zero-capacity container allocation does not return NULL here (16-byte zone). The failing element, the
process and kernel 18's `AMFI DIAG` lines are not on the panic screen.

### Prepared run 27 (2026-10-27): kernel 19, AMFI conversion note on the panic screen

Diagnostic only, gated by the existing `native-entitlements-diag=1`. OS profile patch
`native-amfi-panic-note.patch` (end of the 22H384 series; generated against the kernel 18 work tree):
`native_panic_note[256]` in OSRuntime.cpp holds `exec=<vnode name> der=<DER entitlement bytes>
fail=<factory>(<args>) last=<last bounded string>`. `ubc_cs_blob_add` sets exec/der right before
`mac_vnode_check_signature`; `OSString::withCString(const char *, size_t)` records each string and the first
failure; OSDictionary/OSArray::withCapacity, OSSymbol::withString, OSNumber::withNumber and OSData::withBytes record
their first NULL return. UniXNU `scripts/xnu-panic-screen.c` + `patch-xnu-entry-probe.py`: an optional
`_native_panic_note` symbol is printed under the frames. The panic-screen cave had about 5 bytes free, so a kernel
with the note prints it instead of the `pages=`/`free=` lines (0x9d0 bytes; kernels without the symbol are
unchanged). Bootstrap link failure in the log is the expected first pass.

| Artifact | SHA256 |
| --- | --- |
| `build/kernel/ipados-18/kernel-k19.macho` | `3ad001775d79408a4190dfee2047961f305a23bbca30a1b5795dbdfdc3ddd2ec` |
| `build/payload/ipados-18/payload-k19-amfinote.img` (eight-core, run 26 Data seed `e3b1ea02…`, trust cache `26ed8386…`) | `21e5e2f399c222784cbd50399f1c532c5db081c975f23be9078ba127a332c798` |
| `build/boot/blueline-unixnu-mu-simplefb-xnu18-k19-amfinote.img` (firmware d76fc119…) | `b726c86fee398d7963e84becdb521afb3e0ec15bfab914377cb222a9c336c4f0` |
| `build/share/blueline-run27-kernel19.7z` (image + `8-test-kernel19.txt`) | `e7ba013b88178463c27a15f9d233710205c3e12712380e7f466c793f0ae86ae4` |

On the Mac the canonical `payload.img/json` were restored to kernel 18's eight-core build; `kernel.macho` is kernel 19
(`kernel-k19.macho`). Not run on the phone yet.

### Run 27 result (2026-10-27): AMFI's stack check assumes 16 KiB pages (root cause of status 14)

Kernel 19 note: `exec=sleepd der=3602 fail=- last=Streams` (same AMFI panic and stack as run 26). No factory
failed. sleepd (`/System/Library/PrivateFrameworks/SleepDaemon.framework/sleepd`, 22H384 OS, read-only mount) has
`com.apple.private.intelligenceplatform.use-cases` → `com.apple.sleepd.inBedDetection` → `Streams` → array of two
strings; the DER (file 0xe00f, 3602 bytes, no 4 KiB boundary inside) is ordinary (`30 2d 0c 13 …`). Earlier
top-level arrays of two strings converted.

AMFI's CoreEntitlements array iterator (kernelcache 0x…5a844f8) starts with a stack-headroom check:
`if (((sp + 0x50) & ((1 << PAGE_SHIFT_CONST) - 1)) < 0x80) return 2`. With 16 KiB pages a 16 KiB kernel stack is
one page, so this is "less than 128 bytes of stack left". The native kernel has 4 KiB pages
(`PAGE_SHIFT_CONST` 12) and 16 KiB stacks aligned to their size (`KERNEL_STACK_SIZE`, `kernel_stack_mask`), so the
check fails whenever sp lies in the low 128 bytes of any 4 KiB page of the stack: here at sleepd's nested array,
so the builder returned NULL and AMFI panicked with status 14. AMFI reads `PAGE_SHIFT_CONST` in only one other
place (0x…5a7f5f4, rounding a size to the page size, which must stay 4 KiB), so the fix belongs at the check
itself (shift = log2(kernel_stack_size)), not in the variable.

### Prepared run 28 (2026-10-27): kernel 20, AMFI stack-check fix, no colour bars

- AMFI: UniXNU `scripts/patch-amfi-stack-headroom.py` (payload step, when the OS profile sets
  `[payload] amfi_stack_headroom_shift`; 22H384: 14) finds the 9-instruction check once in the prelinked kernel and
  replaces `ldr w8, [x8]` (the PAGE_SHIFT_CONST load) with `mov w8, #14`; file offset 0x1d92524
  (`08 01 40 b9` → `c8 01 80 52`), idempotent. AMFI's page round-up use of PAGE_SHIFT_CONST is unchanged.
- No static colour on the OLED panel (owner): device series no longer applies `zzz-blueline-stage-bands`,
  `bsd-steps`, `bsd-steps2`, `status-marks` (files kept); `board.toml [payload] entry_probe_bars = false`
  (UniXNU `patch-xnu-entry-probe.py --no-bars`, panic screen at the unchanged address); DarwinBoot
  `DARWINBOOT_HANDOFF_BAR` (default 1) is 0 for blueline (`uefi-blueline` blueline.dsc BuildOptions), so
  `DarwinEnter` gets no frame buffer and paints no green band. boot_progress is compiled out (no BootProgress).
  Kept: panic screen text and the kernel 19 AMFI note (it replaces pages=/free=).

| Artifact | SHA256 |
| --- | --- |
| `build/firmware/blueline-mu-simplefb-debug.fd` (no handoff band; previous: `blueline-mu-simplefb-debug-d76fc119.fd`) | `3e744b3cbc5de99f44d6ab85a1c433f557804173b8bdb504a9abfaf33210b7f2` |
| `build/kernel/ipados-18/kernel-k20.macho` | `56c89384ed057f7252099ba63611b7a3f5387c082673742dc825f32a0ff1926f` |
| `build/payload/ipados-18/payload-k20.img` (eight-core, Data seed `e3b1ea02…`, trust cache `26ed8386…`) | `a5c7bbe615894912aca955574bc5d3f941ff1e97dc0bdafedb2871b4396c7d97` |
| `build/boot/blueline-unixnu-mu-simplefb-xnu18-k20.img` | `45cbe1c1c5e4f2103db9b867d70e20c006aaeb3fa3ea85454f7a0eda7f11eaab` |
| `build/share/blueline-run28-kernel20.7z` (image + `9-test-kernel20.txt`) | `9ad32564bc5dd386eedc6716b72ae25072d6bad1e5a826d6f8e2cbb424cff167` |

Not run on the phone yet.

### Run 28 result (2026-10-27): past AMFI; eight-core assert `not_in_kdp` in pmap teardown

Kernel 20 (eight-core image): no colour bars (only DarwinBoot's hand-off text). sleepd no longer stops the boot.
Panic: `pmap.c:3577 Assertion failed: not_in_kdp` (assert trap, esr 0xf200bffc, header x8 0xfffffff0072435bc),
note `exec=AirPlayHalogen der=0 fail=- last=com.apple.usernotificationsd`. Frames (kernel 20): `pmap_tt1_deallocate`
assert in `pmap_destroy_internal` ← `pmap_destroy` ← `vm_map_destroy` ← `posix_spawn` ← `unix_syscall`: a failed
spawn's address space being torn down. `not_in_kdp` is cleared only inside `handle_debugger_trap` (osfmk/kern/debug.c,
e.g. a stackshot) and restored after; real hardware halts the other CPUs first. Working hypothesis: a debugger
operation (likely a stackshot) ran on one core while another core, not halted (cross-CPU halt unverified on this
SoC with 8 cores), kept running. Proposed test: the same kernel with `cpus=1 sheng-smp=0`.

### Prepared run 29 (2026-10-27): kernel 20, one core

Owner: one core until SpringBoard. `board.toml` now sets `cpus=1`, `sheng-smp=0` (restore `cpus=8`, `sheng-smp=1`
after SpringBoard). Same kernel 20, firmware `3e744b3c…`, root and Data seed. Payload
`build/payload/ipados-18/payload-k20-onecpu.img` `2519de22f75c7b3f98c1e825c6a35bb0cd7f30c55fc25fa89876432d1babe878`;
boot image `build/boot/blueline-unixnu-mu-simplefb-xnu18-k20-onecpu.img`
`d862b2bc09b14ccce8c134b50d7100cea0cc56eea58892c958641bb083e52171`; share `blueline-run29-kernel20-onecpu.7z`
`428b9b380c8a7337cace5cac890281d98b9664d599f63af8dc8b60927ce1bfb6`. Writable `/private/var` (persistent Data,
state 3 first boot / 2 resumed) is configured since kernel 18 but not yet observed on the phone.

### Run 29 result (2026-10-27): one core, no panic, screen unchanged for 10 minutes

The kernel 20 one-core image left DarwinBoot's hand-off text and UEFI's last line (`SecurityLock::LockType:
SOFTWARE_LOCK ... Lock Capsule Interface`) on screen for about 10 minutes; no panic screen. The XNU console has
never drawn on this panel (runs 26-28 showed only DarwinBoot text above their panic screens) and userland has no
display driver, so this does not separate an early hang from a silent boot. Check from TWRP, read only
(`build/share/11-check-data-store.txt`): the RamBlock persistent store header `SHNGDATA` at LUN 0 LBA 0x98347f
(generation at byte 56, CRC at 60; `drivers/RamBlock/PersistOverlay.h`), and nonzero bytes in its 640-block bitmap
(80 GiB / 4 KiB pages). The area held old Android data before, so the magic can only come from this kernel.

Store header read back from TWRP after run 29: `SHNGDATA`, version 1, header 0x40 bytes, 0x01400000 pages
(80 GiB), seed identity `023d33e855bd5c98…5648e4602877523c57` (this image's), generation 1. The one-core kernel 20
boot reached the persistent Data store (fresh open) without panicking; the silent screen is the missing display path.
Bitmap: 9465 nonzero bytes of 640 blocks, i.e. at least 9465 pages (~37 MiB, at most ~296 MiB) of /var written
to the store: userland ran and wrote to the Data volume. Second boot of the same image: header unchanged
(generation 1; RamBlock keeps a valid header on resume and bumps the generation only when it re-creates a damaged or
foreign store) and the bitmap grew to 9825 nonzero bytes: the store was resumed, the first boot's /var writes
survived and the second boot added more. Writable, persistent /var is verified on the phone.

### Run 29 /var read back (2026-10-27): SpringBoard spawns and crash-loops (SIGTRAP); no display path

Read only from TWRP: bitmap (dd → /tmp, adb pull), run list decoded on the PC (78388 pages, 306.2 MiB, 40 runs),
pages dd'd per run into /tmp and pulled (321077248 bytes). `scripts/blueline-data-rebuild.py` laid them over the seed
(`~/blueline/data-readback/run29-data.img` on the Mac); `hdiutil attach -readonly -nomount -imagekey
diskimage-class=CRawDiskImage`, `mount -t apfs -o rdonly,nobrowse,noowners` (volume BluelineData, UUID 789552B2…,
unencrypted). The clock is 1969-12-31 (no RTC time).

- `/var/log/com.apple.xpc.launchd/launchd.log*` (9666 lines, two boots): SpringBoard (user/501) and backboardd spawn
  about 16 s into launchd's log; backboardd stays running. SpringBoard exits `SIGTRAP | sent by exc handler` after 12 s,
  then after < 1 s each relaunch (32 times). Also crash-looping with SIGTRAP: liveactivitiesd (29), cameracaptured (10).
- 62 `stacks+bluetoothd` stackshots (bug_type 288, "LeSecurityManager hit a keychain problem": no SEP keychain);
  103 processes alive at 16:19. No other crash reports.
- Unified log (`/var/db/diagnostics` + `uuidtext`): macOS `log show --archive` decodes nothing ("partial or missing
  metadata"), so SpringBoard's trap reason is not read yet.
- erikkunz/pixel3-ios reached SpringBoard on this phone with framebuffer services (an AppleCLCD stand-in presenting
  into ABL's framebuffer at 0x9D400000). This port has no display component; UniXNU's `GopFramebuffer`
  (MobileFramebuffer over the UEFI GOP framebuffer) is verified on sheng for 21H461 only and is not selected here.
  Likely, not confirmed: SpringBoard traps for want of a display.
- Unified log decoded with mandiant/macos-UnifiedLogs `unifiedlog_iterator` v0.7.0 (x86_64-apple-darwin release,
  sha256 32e23956…7f1a, in `~/blueline/tools-dl/` on the Mac; `--mode log-archive` on a copy of /var/db/diagnostics +
  /var/db/uuidtext): 49702 entries. **Confirmed:** every SpringBoard instance's last message is
  `failure in _initWithBookendObserver:transformer: of <FBDisplayManager> (FBSDisplayMonitor.m:56) : failed to find the
  main CADisplay`, then SIGTRAP. IOMobileFramebuffer: `FB Count = 0`; backboardd: `Built in display/backlight driver has
  not been found`, `StartWindowServer: headless`; thermalmonitord `IOMFB failure 0xe00002c0`; other daemons time out
  waiting for the main display layout. The port needs an IOMobileFramebuffer-compatible display driver (main CADisplay).
- GPU: the Pixel 3 has an Adreno 630 (a6xx). refs/ios-flame (Pixel 4, Adreno 640, iOS 16) renders SpringBoard and apps
  through Metal on Mesa Turnip via metal2vulkan; UniXNU has the same frontend (`AdrenoMetal`) but its kernel GPU driver
  is `Adreno740` (a7xx, sheng, 21H461). A GPU desktop still needs the display driver to scan out frames.


### Display port: survey (2026-10-27, in progress)

Owner: display first, then the Adreno 630 GPU. UniXNU `GopFramebuffer` (revision 33e941a on the Mac): the working
path is `NativeLegacyDisplay`, a subclass of the original `IOMobileFramebufferLegacy` (IOMobileGraphicsFamily) that
reuses the original family code and presents into the GOP framebuffer (CPU copy, optional GPU hand-off, timer vsync
`sheng-vsync`); `NativeMobileDisplay` is an unpublished prototype. Its ABI (`OriginalLegacyFramebufferABI.h`, the
`native_legacy_*` bindings to original functions, object offsets) and `scripts/audit-mobile-graphics-legacy.py` are
pinned to 21H461 (Legacy vtable 358 slots, abstract slots 211, 213, 245, 246, 306, 307, 337, 338, 345).
22H384 (`~/blueline/ios18-tools/classvt.py`): IOMobileFramebuffer vtable 0xfffffff006d4c4b8, 266 slots;
IOMobileFramebufferService 0xfffffff006d4cdb8, 266; IOMobileFramebufferLegacy 0xfffffff006d4fd00, **368 slots**;
IOMobileFramebufferUserClient 0xfffffff006d4b4a0, 187 (kernelcache addresses). The layout grew (21H461 base 262,
Legacy 358), so every slot, abstract leaf, binding and field offset has to be re-derived for 22H384.

Legacy vtable alignment 21H461 → 22H384 (fingerprints of each slot's code, difflib; script
`vt_align.py`, map in `~/blueline/data-readback/legacy-vtable-map.json`; 21H461 constructor at kernelcache
0xfffffff0067865d8 = the audit's candidate address + 0x1000000): slots 0-167 (IOService) keep their positions; the
abstract leaves map 211→212, 213→214, 245/246→247/248, 306/307→314/315, 337/338→347/348, 345→355. The family's own
methods were recompiled (no byte match), with slots inserted: +1 in 168-210, +1 in 214-244, +6 in 247-305, +2 in
308-336. Still to identify by a finer match: beforeExternalMethod (old 182), releaseClientState (193),
getDisplayDimensions (249), the refresh attributes (301), newUserClient's position, the field offsets and bound
original functions.

Upstream check (owner: fetch UniXNU often): UniXNU main is one docs commit ahead (120067c), still pins GopFramebuffer
33e941a, and has no iOS 18 profile. Branch `feat/ipados27-gpu-parity` (290 commits, history not shared with main) adds
iPadOS 26.5.2/27.0.1 profiles (arm64e: not possible on SDM845, which has no pointer authentication) and GopFramebuffer
branch `feat/ipados27-gpu-parity` adds half-float frames. `feat/adt-product-identity` and `feat/oslog-export` exist too.
The missing display pieces are in the public device repo `UniXNU/UniXNU-sheng` (refs/UniXNU-sheng, 08cde20):
`scripts/build-gop-legacy-display.py` generates `OriginalLegacyFramebufferABI.h` from the 21H461 audit and hard-codes
private offsets of the original class (object size 0x18a0, swap queue +5472/+5480/+5488, request links +2960/+2968,
boot dimensions +5808/+5812, slots 249/295/312); `package/LegacyDisplayProbe.cpp` (585 lines) holds the observers.
Porting to 22H384 means re-deriving all of them. The alternative is ios-sweet's standalone display (refs/ios-flame
`0046-sweet-iosurface-iomfb.patch`, 1422 lines: own AppleCLCD + IOMFB user client + IOSurface root, iOS 16), which
would replace Apple's IOMobileGraphicsFamily instead of reusing it and does not provide UniXNU's GPU hand-off.

iPhone layout (owner, 2026-10-27): to be done through the identity iOS reads (MobileGestalt device class / artwork
idiom, possibly an iPhone product identity in the ADT as erikkunz/pixel3-ios did with an iPhone root image).
Visible only once the display works; a changed Data seed resets the persistent store. The run 29 MobileGestalt cache
directory is unreadable offline (data protection).

8 cores (owner question): not yet. Run 28's not_in_kdp assert came from a stackshot racing unpaused cores, and the
run 29 /var shows bluetoothd requesting a stackshot every few seconds (62, "keychain problem"); one core ran 20+ minutes.

### Handoff checkpoint (2026-10-27)

Prepared the current Claude resume at the top of ignored HANDOFF.md, preserving
historical notes below it. Reconfirmed the build Mac branch/base, stable Data seed,
final kernel/archive hashes and existing download server. No new build, phone test
or verification-level promotion: run 26 remains prepared and awaits the owner.
Next agent: reuse kernel 18 and the exact seed, collect and record the phone
Data/AMFI result before making another change.

## GPU continuation — 2026-10-27 (host work, no new phone run)

The owner requires GPU rendering for the next test and UniXNU's architecture,
using NWylynko/ios-flame as an A6xx reference. Its working Metal path itself
uses UniXNU's Darwin Turnip backend and native metal2vulkan bridge. Its wire
ABI remains version 1/16 IBs, whereas this port keeps UniXNU version 2/128 IBs.

Seven stock A630 firmware files are pinned and verified. Legacy HFI passes
54 compiled AArch64 wire/fault checks. Shared A6/A7 ring, CP and context tests
pass; A630 SQE maps at 0x400000 and a one-stream fixture preserves secure
SID 1. The SDM845 kit adds bounded native MMIO/time binding, GPLL0 preflight,
legacy power/DCVS handshakes and A630 register setup. Platform ASan/UBSan:
988 checks pass. An executed fixture trace matches 105 pinned Linux A630
HWCG writes, 32 protection spans and 14 hardware policies. Command-db parser:
902 bounds/fault checks pass. Device OPP/BW policy: 21 synthetic-rail checks pass.

The common core compiles as 30 ARM64/iOS XNU objects with -Werror, and the
separate device policy also compiles with the actual XNU C recipe. Reports
record commands and source/object hashes in build/gpu-tests/*build-report.json
and the Mac resolved tree's build/{sdm845-gpu-core,blueline-gpu-policy}.
All of these are host qualification results. No live clock/command database,
SMMU ownership, ZAP authentication, CP execution, Metal or scanout is claimed.

Full driver/display/Metal integration and phone qualification remain pending.
Commands, source revisions, board differences and remaining work:
[GPU-A630](GPU-A630.md). Kernel 20 one-core/run 29 remains the last phone
candidate. No CPU-rendered display image, kernel 21 or new phone run was made.

## GPU candidate build — 2026-10-28 (Claude; host/build only, no phone run)

Owner: first phone test is the full candidate (Metal pipeline, GPU scanout), no CPU rendering, one core, no bars.
Built on the Mac, all with the resolved tree's XNU recipes and -Werror:
- `package/BluelineGpu.cpp` (A630 bring-up on kits/Sdm845Gpu, zap PAS 13, CP fence checks, native Adreno publication,
  Metal accelerator; `blueline-gpu=2`), `package/build-blueline-gpu.py`.
- `package/build-blueline-accelerator.py`: AGXAccelerator with 22H384 IOAcceleratorES bindings (C2 0xfffffff004f40704,
  D2 0xfffffff004f40750, gMetaClass 0xfffffff006beced8; kernelcache - 0x1000000, fixed prelink slide).
- `package/build-blueline-display.py` + `package/BluelineDisplayProbe.cpp`: GopFramebuffer NativeLegacyDisplay for
  22H384 (368 slots, 203 byte-checked bindings, CPU copy fallback removed), GPU hand-off presenter, display-scale 3.
- `package/build-blueline-kernel-archives.sh` merges the archives XNU_NATIVE_ADRENO / XNU_NATIVE_LEGACY_DISPLAY link.
- Turnip (UniXNU Darwin KMD, pinned Mesa) builds for iOS: 17,878,744 bytes.
Kernel 21 (defconfig NATIVE_ADRENO + NATIVE_LEGACY_DISPLAY, display/GPU boot args): first link failed only on three
optional sheng hooks (boot_log_export_put, native_ufs_dpu_map/state), now defined. Kernel 21 links:
`kernel-k21.macho` SHA256 24112201286b5169a4e27d3672e3b3cd875d20bea722ac69cb65fca9a6383582; accelerator and display
bindings re-checked against it (PASS).
Still to do: Metal plugin (Rust/LLVM toolchain on the Mac), GPU addon, owner trust bundle
(`package/make-blueline-trust-bundle.py`), payload and boot image.

### Prepared run 30 (2026-10-28): kernel 21, GPU candidate

- Metal plugin: `scripts/build-adreno-metal-plugin.py` without the A740 Turnip replay step (host-simulation only);
  needed a glslc shim over glslangValidator, SPIRV-Tools spirv-val, Rust 1.98.0 + aarch64-apple-ios, MacPorts
  LLVM 22 llvm-as, and legacy-name links drivers/NativeMobileDisplay, NativeMetal, NativeAdreno on the Mac.
  CDHashes: plugin c278387b…ad85, adrenoprobed b61d1439…68e4, Turnip 5c058b63…44f5.
- Trust: `package/make-blueline-trust-bundle.py` -> 4442e2b6…c728 (owner module + both 22H384 modules); added to the
  OS profile's trust_cache_sha256. GPU addon: `make-gpu-addon.py --publish` (9 files, 12,172,464 bytes).
- Payload (k21, one core, display/GPU args, existing Data seed): 19cf17bb8fabb3905e283a3e9639704a7f008a0f5235357b1dc6f5abd6496def.
- Boot image: `build/boot/blueline-unixnu-mu-simplefb-xnu21-gpu.img`, 84,951,040 bytes,
  aa2e59717686f4cba522d7139d6a8a480aa85f9beddbb51f0d2f5046a3bcfcc0 (firmware 3e744b3c…, no bars).
- Archive `build/share/blueline-run30-kernel21-gpu.7z`, 20,582,380 bytes,
  ec46672109e783e5e42703de869b31271b3d841d99e0dbc8e004aaadbd957a6b. No phone result yet.

### Run 30 result (2026-10-28): display found by backboardd; GPU stopped at the GPLL0 preflight

/var read back (108,762 pages; `~/blueline/data-readback/run30-data.img`, log `run30-log.jsonl`, boot A6AF0EC3):
- `BluelineDisplay: GOP display 1080x2160 started`, registered; backboardd `FB Count = 1`, CADisplay 1 created;
  IOMobileFramebufferUserClient opened 7 times (status 0). SpringBoard no longer crash-loops on a missing CADisplay
  (pid 26 killed once by launchd, pid 146 still running at the log end, 00:02:02).
- backboardd: `Display 1 current mode (16383 x 16383), preferred mode (0 x 0)`; DisplayAttributes, IOMFBDisplayRefresh
  and other IOMFB properties not found. 0x3fff is not in the 22H384 family kext: a userspace default. To fix next.
- `BluelineGpu: cmd-db at 0x85FE0000, 0xDC0 bytes`, version 1; `CX power: status 3 phase 1 window 0 offset 0x38 value
  0x4000`: Sdm845GpuPowerCx's GPLL0 check refused. msm-4.9 runs GPLL0 in FSM vote mode (GCC 0x52000 bit 0), so OUTCTRL
  (mode bit 0) is clear.

### Prepared run 31 (2026-10-28): kernel 22

kits/Sdm845Gpu accepts lock + (OUTCTRL or FSM vote bit 20); BluelineGpu logs the GPLL0 words on any clock failure.
Kernel 22 b8dde57857a912a6ce112d93fef970456dc87deb2e7c8977a8354f5d6a41dc10 (the Mac's kernel-k21.macho was overwritten
by the rebuild; the original k21 is build/kernel/ipados-18/kernel-k21.macho), payload 912e4345…c822, boot image
`build/boot/blueline-unixnu-mu-simplefb-xnu22-gpu.img` 39beffa4f5a7ffaab685276bc0e6b1a4f08bca06772aba3caf74b52e3c18488d,
archive `build/share/blueline-run31-kernel22-gpu.7z` 12b2a08c659a65a527437c7d85065a05d17f700a180d9867cc90d8b56cb690e0.

### Run 30 display mode: not a problem (2026-10-28)

The `Display 1 current mode (16383 x 16383)` line is QuartzCore (22H384 shared cache, format string 0x184686672, logged
at 0x1844b51a8): it unpacks a mode ID as width = id & 0x3fff, height = (id >> 14) & 0x3fff, so 16383x16383 is an empty
(all-ones) current-mode ID, as for a built-in panel without a mode list. The same run 30 log then shows backboardd using
the real display: `StartWindowServer: initial main screen size:{1080, 2160} scale:3`, `main display width 1080 height
2160 framebuffer width 1080 height 2160 scale 3`, `layer position 540 1080 bounds 0 0 1080 2160`. No change needed;
the iPhone-sized scale 3 is in effect. Tools: scratchpad dsc_find.py / dsc_xref.py (direct subcache search; Xcode's
dsc_extractor does not read this cache format).

### Run 31 result (2026-10-28): panic on display leaf 355 after a swap wait timeout

Photo: `NativeLegacyDisplay: leaf=355 caller=fffffff004c1ecf8 stage=10 @BluelineDisplayProbe.cpp:125`. The caller is
IOMobileGraphicsFamily `swap_wait_gated` (kernelcache 0xfffffff005c1ecf8): after `CommandWake didn't happen even after 5
seconds ... cancelling swaps` it logs `swapId, options, noBlock, HPD, DPLink (= slot 355), TimingEnable, PowerStates-Cntr,
Display` and then calls slot 356. So a client's swap never retired: the GPU hand-off did not complete the frame. Fix for
the next build: slot 355 `dpLinkState()` returns 0 (abstract leaf, log value only). The GPU stage is in the /var log.

### Run 31 /var read back and prepared run 32 (2026-10-28)

/var: 114,948 pages (`~/blueline/data-readback/run31-data.img`, `run31-log.jsonl`, boot 2D75EC73). The GPU stopped at the
same GPLL0 check; the dump shows why: `GPLL0 mode D0100005 L 1F user 103 opmode 0 frac 4000 vote 11`. Lock, OUTCTRL and
FSM vote are set (the run 30 guess that OUTCTRL was clear was wrong); OPMODE reads 0 because the FSM runs the voted PLL.
The display still started and backboardd opened it. Fix: kits/Sdm845Gpu accepts OPMODE 0 in FSM vote mode.
Kernel 23 f64f70cd899b657e643d606b4421de5e5a546304989c40fa5a46613b8e38e3d5 (GPLL0 fix + display slot 355), payload
a939c398…ffe8, boot image `build/boot/blueline-unixnu-mu-simplefb-xnu23-gpu.img`
11d9571e6520963e47465e9810e4955701e14403d690ec2c6b94f3a96dd2804c, archive `build/share/blueline-run32-kernel23-gpu.7z`
0ecabb4e7e6798234fd380207ec83174ac62622127ea48f5faafa1667dc1b0c2.

### Run 32 result and prepared run 33 (2026-10-28)

Run 32 (kernel 23): black screen, no reboot. /var (one-command `scripts/blueline-dump.ps1`; Mac `~/blueline/ios18-tools/
readback.sh 32`, boot 5C22B695): GPLL0 passed; `CX power: status 4 phase 1 window 0 offset 0x7100C value 0x80004221`:
the voted gcc_gpu_memnoc_gfx_clk never ran because the kit enabled it before gpu_cx_gdsc (Linux a6xx_gmu_resume does
pm_runtime_get_sync first). `BluelineDisplay: GPU frame failed -1`: a frame reached the display; the black screen was a
blank frame, not GPU output.
Run 33 (kernel 24 670104eaeef1c9205aee007483baba17f634d33ef82e63d7b93fb3c4d6fe4d27): kit order cfg_ahb, CX GDSC (poll
gds_hw_ctrl 0x1540 PWR_ON), then DDRSS/memnoc/DVM branches (986 host checks pass); on-screen GPU status lines with failure
register dumps (iso_font, rows from y 1180, redrawn every 2 s until the first GPU frame); GPU/GMU SMMU slot and bank picked
automatically when taken. Payload d7739c6a…0693, boot image `build/boot/blueline-unixnu-mu-simplefb-xnu24-gpu.img`
414ccb30facb5b5303b03e7a9037b85ed9a7627f53c756ced0191a9da6468fca, archive `build/share/blueline-run33-kernel24-gpu.7z`
35ea238d2c897961123cecb0cba2ceb4676186d3c168918c1a6b3b10de8f5245.

### Run 33 result and prepared run 34 (2026-10-28)

Run 33 (kernel 24): loader screen, no status text (the status width check wanted 1104 px on the 1080 px panel; fixed in
052df46). /var readback (`readback.sh 33`): six boots, none of them run 33. Boots A6AF0EC3/2D75EC73/5C22B695 are runs
30-32 (kernel build "Oct 27 23:51", FB Count 1, BluelineGpu lines); 632E778D/C0D288FB/90CC2441 are older kernels
("Oct 27 18:55", FB Count 0). So kernel 24 left nothing in /var. Its only new code runs in the GPU thread, so the likely
cause is a freeze on the single CPU after the CX step, which run 32 never passed: the first access to the GPU SMMU, the
GMU, the RSCC or the PDC. A freeze there loses all unflushed /var logs. Addresses checked against Linux sdm845.dtsi: GMU
0x506a000/0x30000 (RSCC +0x23000), PDC 0xb280000, PDC sequence 0xb480000, GPUCC branch and RCG offsets (gmu_clk_src 0x1120
src 6 div 1.5, cx_gmu 0x1098, cxo 0x109c) match.

Run 34 (kernel 25 a5cf8c23a54827f2d833326496c43fe2ca015ae8557f56469335f9648590e22a):
- UFS diagnostic log (`package/BluelineLog.c`, `sheng-log=1`), a trimmed port of fajita/sheng `BootLog.c` with the same
  SHLOG001 v4 window. It holds the kernel message buffer and a text slot (GPU and display state, on-screen status rows),
  written every 2 s and synchronously on every GPU status line. The window is the last 8192 blocks of darwin, absolute LBA
  0x1dc1700 (31201024). The Data store ends below the 1 GiB reserve, so they don't overlap. Started from the display's
  early probe. Left out: the exports, the board page, autoreset, and the panic flush (no panic hook on blueline yet).
- GPU status lines are drawn synchronously, 20 rows. Each line also goes to IOLog and a log checkpoint. A line is added on
  the first access to each kit MMIO window and on each kit phase change, before the access. Another line marks the first
  GPU SMMU read.
- `scripts/blueline-dump.ps1`: the default is now the 32 MiB log window (`-Data` adds the old /var dump).
  `scripts/blueline-log.py` decodes it on Windows.
Payload ab82c40e8958b474d5b462f0291c10d89208dfdc546a8df689322d4f0bb61001, boot image
`build/boot/blueline-unixnu-mu-simplefb-xnu25-gpu.img` 33729ccc48e6cd32d16b02b673af76306c3bd220f7f4019297d3e62f5ab5559b,
archive `build/share/blueline-run34-kernel25-gpu.7z` d9af5bba8a0bb9eab644e94f86327b7aa377d0f100c554736207ef31ff74988b.

### Run 34 result and prepared run 35 (2026-10-28)

Run 34 (kernel 25), photo: status text works. The last lines are `GPU 4 CX power`, `ph 1 r gcc+0 first`, `ph 2 r gpucc+106c
first`, `ph 3 r gcc+52004`, then `CX on; reading the GPU SMMU`, and the phone froze (line 0 stopped at `log 2 r12`, display
0 frames). So CX power passed every poll, and the first GPU SMMU access hung the CPU. `smmuSurvey()` read the SMMU through
a pointer with volatile cast away, and the compiler emitted `ldp w9, w10, [x0, #0x20]` (ID0/ID1). The SDM845 hypervisor
traps SMMU accesses (Linux arm-smmu-qcom's S2CR quirks come from that emulation) and can only emulate single loads and
stores with a valid syndrome. The stock DT's GPU SMMU (arm,smmu-kgsl@5040000) needs only gcc_gpu_memnoc_gfx_clk and
gpu_cx_gdsc (0x509106c, hw-ctrl 0x5091540), both already on. Every other pair load or store in BluelineGpu.o hits normal
memory; the kits use single volatile accesses.

Run 35 (kernel 26 e1dfa385af55fc6592308ec71d085b3ecd1859678a5f865aff6fb2607f2b9947): single volatile SMMU reads, plus status
lines after the first one (`smmu scr0`) and after the ID registers. Payload 87876daa…e906, boot image
`build/boot/blueline-unixnu-mu-simplefb-xnu26-gpu.img` 9bc2b29e96c20574d58efb2eb8cb7d939f8041875a98c6551b12121dba69e1da,
archive `build/share/blueline-run35-kernel26-gpu.7z` fb4405d7447411ef614c637b15afb8b06fcec39367036c63b6c96ca398827a9e.

### Run 35 result (2026-10-28)

Run 35 (kernel 26), photo: the SMMU freeze is fixed. GPU SMMU sCR0 0x200626, ID0 0x48016605 (5 SMRs, stage 1 only),
ID1 0x20008005 (5 context banks, no stage-2 banks), ID2 0x7511 (IAS/OAS 36 bits, AArch64 4K/16K/64K). GMU SMMU bank 1,
slot 1 were picked. Then `FAIL 4 GMU context: e00002ca` (kIOReturnIOError from NativeAdrenoGmuContext::bringUp). Its step,
program and route report is only in the kernel log; awaiting the UFS log readback. The next build shows that report on
screen. Later the render server's frames (disp 4, gpu-ref 3, xfer 4) cleared the status rows; only line 0 kept being redrawn
(log 2, rounds rising). The status thread now redraws every row.

### Run 35 readback and prepared run 36 (2026-10-28)

The UFS log of run 35 (boot id 1f95fe75) has 169 rounds over 314 s and no write errors. The status slots show the failure,
but user space ("ustate: caching container path") had filled the 128 KiB message buffer, so the report line was gone. Kernel
27 (aa2a7fe, never run) added a driver log ring to every slot and the full readback on screen. The /var readback
(`readback.sh 35`, boot 3A645CF5) has the line: `GMU context bank 1 slot 1: step 4 status 0xE00002CA program 0/5 route 10/0
ttbr0 0x10000C0702000`. Step 4 is Attach. The context bank programmed and enabled (status 0, phase 5); ArmSmmu4KAttachStreams
returned Busy (10) from its scan, which takes only a slot whose S2CR is FAULT (Linux's arm_smmu_device_reset state for unused
entries). The firmware left another value behind slot 1's invalid SMR.

Run 36 (kernel 28 f75f131572ad4c80194587bc380854ed2580a2be9d769642e13a9e66f0fdef2e): `faultSlot()` resets an unused slot
(SMR invalid) to S2CR FAULT before an attach, falling back to a BYPASS write that arm-smmu-qcom says the SDM845 hypervisor
stores as FAULT. Every SMR/S2CR goes to the log ring; a GPU context failure goes on screen. Payload def9f0f4…3491, boot image
`build/boot/blueline-unixnu-mu-simplefb-xnu28-gpu.img` 6e6ecdc24f1274630af05fa1392065e1890042fd2b344f89baffac9797c1fbe7,
archive `build/share/blueline-run36-kernel28-gpu.7z` b4645054f15e24d9d67e6174e4144b50d6dc55d2629336f94db2cb2b43ab319b.

### Run 36 result and prepared run 37 (2026-10-28)

Run 36 (kernel 28), photo: slot 1's S2CR read 0x200ff (FAULT with CBNDX 0xff). The FAULT write was ignored and the BYPASS
write was stored as FAULT (0x20000), the SDM845 hypervisor quirk arm-smmu-qcom documents. The GMU context attached, HFI
queues mapped, and GMU boot started: the first writes to GMU, RSCC, PDC sequence and PDC all went through. Then
`kit 4 ph 5 w2 +24c20 =0`: a timeout in the Sequences phase polling GMU_RSCC_CONTROL_ACK (0x9308) for bit 1. Clocks at
failure: gcc 71004/44038/7100c/71018 running, gpucc gdscr 0xf8222000, gds_hw_ctrl 0x80001fe0 (on), cx_gmu 0x4ff1, cxo 1,
gmu_clk_src cfg 0x602.
Linux 6.12 programs the RSCC/PDC in a6xx_gmu_rpmh_init, which ends with a6xx_rpmh_stop (REQ 1, RSCC STATUS0_DRV0 bit 16,
REQ 0); the power-on request (a6xx_rpmh_start) comes later. msm-4.9 kgsl sends no power-on request on the first boot.
UniXNU-sdm845-common 3fd0e59: the kit now stops the RSC first, and neither handshake aborts the boot; both are
recorded in Sdm845GpuState and shown on screen ("rsc stop .. start .."). The GMU firmware's 0xbabeface handshake decides.
992 host checks pass.

Run 37 (kernel 29 3237ded4c8d567b7d5d790081ecdceb1d1fd06e6ee42338a0da51cfc076965ff): payload 08de6a79…eab9, boot image
`build/boot/blueline-unixnu-mu-simplefb-xnu29-gpu.img` 0093799a5a103fb0e82a383672a7c582a6367d7dfa660e2bad1fc80853b23a6d,
archive `build/share/blueline-run37-kernel29-gpu.7z` f37061fb664fdde132e112c85903a2d953a16e3818e84953b99cbbfa7e91400f.

### Run 37 result and prepared run 38 (2026-10-28)

Run 37 (kernel 29), photo: the GPU RSC stopped (`rsc stop 1 1010006`, STATUS0 bit 16) and started (`start 1 3`, ACK bit 1).
The GMU firmware loaded into ITCM (ph 6, gmu+3000), started (ph 7) and passed its boot handshake. The GX/MX rail vote (ph 9)
and SPTP (ph 10) completed. HFI (ph 11): `kit 4 ph 11 w2 +14600 =1`. HFI_CTRL_STATUS (0x5180) read 1 (ready), but poll()
checked the deadline before the value, and the single CPU had been away past 10 ms. UniXNU-sdm845-common f048c75: the value is
checked first, as in Linux read_poll_timeout. The shared AdrenoFirmware HFI startup loop does the same after a reply (not
changed: its budgets are 100 ms per command and 1 s in total).

Run 38 (kernel 30 a067f16b6936acd2b3174d8fbd271369cc8459d4d3051fa1c6116b138fa99828): payload 46563761…4f8d, boot image
`build/boot/blueline-unixnu-mu-simplefb-xnu30-gpu.img` 6e1587375e63039d3612eff618d7c9cc8f1ad6709968f2314462ba0210cb418d,
archive `build/share/blueline-run38-kernel30-gpu.7z` 78274b30911c595aa4f85651a2fa0fe985e91db4eb4097475d80d59b20ce819d.

### Run 38 result (2026-10-28): A630 up, Metal accelerator registered

Run 38 (kernel 30), photo: every bring-up stage passed. HFI start (8), GPU_SET (9), GPU SMMU bank 0 / slot 0 (S2CR0
0x200ff, the same hypervisor quirk, handled by the BYPASS write), hardware setup (11), CP/zap/secure (ph 16-17), boot
release (18), `GPU ready: CP fence + zap ok`, publish (19), Metal (20/21), `Metal accelerator registered`. The last line is
`ph 19 r gpu+2018`: the first GPU register access after boot release, a read of CP_RB_RPTR (0x806). The display counter
showed 0 frames at the photo (`disp 0/0`, log round 83). Whether the system kept running past that read is the open
question (a CPU read of a GX register while the GMU has collapsed GX would stall the bus on the single CPU).

### Run 38 readback and prepared run 39 (2026-10-28)

UFS log (boot 3033872e, 669 s, 376 rounds, no errors): the GPU driver log ring holds the whole bring-up through `Metal
accelerator registered` and nothing after. Display words at the end: 4 swaps validated, 0 refused, 3 GPU offers refused,
4 transfers (one ended 0xe00002d8), 0 retired, 0 GPU completions. NativeGOPPresenter takes an offer only while a GPU client
waits in waitSwap. That client is the Metal plugin's scanout thread in backboardd (GpuScanoutClient.h), started only after
the plugin publishes its device. /var (`readback.sh 38`, boot 852092EE) shows backboardd at 106.8 s:
`native-metal: unexpected _MTLDevice ABI` (twice). The bridge pinned iPadOS 17.7.11's `_MTLDevice` instance size, 536 bytes.
22H384's is 600 bytes (superclass NSObject), read from the shared cache's class_ro with the new
`~/blueline/ios18-tools/objc_class_size.py` (Xcode 26's dsc_extractor crashes on this cache, exit 139). The `Sandbox:
backboardd deny iokit-open-user-client AGXDeviceUserClient` lines are the plugin's marker channel (Gpu::mark opens type
0x4147xxxx, which the driver reads in newUserClient before the sandbox check) and are expected.

Run 39 (kernel 31 30380204069310d376d173bf0ced1f6dfa2f137edf31ccfb90d11f6e8b420af3):
- Mac UniXNU `drivers/AdrenoMetal` d49f74c (local): the guard accepts 536 or 600. The plugin was rebuilt; CDHashes are
  plugin 5bbc1760…ab86, adrenoprobed 0374d2ad…1ec1, Turnip 5c058b63…44f5. The trust bundle is
  857dd4fc28ef197bcf2f26a068a03e8d38ce21d97479bb0fd6291901abc4ab14, allowed in the profile (Mac UniXNU 164d863, local). The GPU
  addon has 9 files, 12,172,664 bytes.
- `package/BluelineLog.c`: each slot now carries the plugin's markers, report lines, other clients' failure lines, the
  per-process open records and the bridge's telemetry rows (NativeAdrenoPublish.cpp globals).
Payload e603d3e0…5044, boot image `build/boot/blueline-unixnu-mu-simplefb-xnu31-gpu.img`
bf43e4dda54e8fcc39a8bcc6227e9220656bee1f421b0161ab5b819c92e53d8b, archive `build/share/blueline-run39-kernel31-gpu.7z`
db8c1bf080346373256fa7346774ca5f2a7554c878099493ba9f66de004282b7.

### Latest photo after prepared run 39 (2026-10-28): registered accelerator, no presentation evidence

The owner supplied a photo while asking to continue the previous work. Treat it as the expected run 39 result for
triage, not a confirmed image identity: the visible status text is also present in run 38 and has no kernel number or
boot ID. No run-39 UFS dump or /var readback has been supplied yet.

Visible lines include HFI start, GPU_SET, GPU SMMU bank 0 / slot 0, hardware setup, release,
`GPU ready: CP fence + zap ok`, publish, Metal, `GPU 21 Metal ok`, and `Metal accelerator registered`.
The summary reads `disp 0/0 gpu-ref 0 xfer 0 log 2 r163`; the last status line is `ph 19 r gpu+2018`.

Source interpretation (`package/BluelineGpu.cpp`, `package/BluelineDisplayProbe.cpp`):
- The bring-up path reaches CP/fence/ZAP checks and accelerator registration. Kernel registration does not establish
  userspace Metal device discovery, pipeline creation or a rendered frame.
- `disp` counts swap validations accepted/refused, not presented frames. `gpu-ref` counts refused GPU offers and
  `xfer` counts transfer callbacks. All are zero in this photo. GPU completions have their own UFS-log field.
- `ph 19` is the SoC kit's GpuReady phase, not board stage 19 (publish). Offset 0x2018 is CP_RB_RPTR (dword 0x806).
  The marker is written **before** the read and only on a window/phase transition. It proves neither that the read
  completed nor that it stalled; an unchanged final marker is not a continuous execution trace.
- `log 2 r163` shows the logger had reached writing state and round 163 when the summary was drawn. One photo cannot
  establish whether rounds continued after the final MMIO marker or whether the machine later stopped.

Resume checks: device HEAD `9259009e115ae2c23148b1f86f69c80e409e2a27`; SoC HEAD
`f048c7522bf741a397a003905b0e1974396f4d88`; Mac UniXNU HEAD `93b4a4d350b580e87ac7d987a8e48df89fc20697`;
Mac AdrenoMetal HEAD `d49f74c0748222d5aa683ec193567f94c2458d06`. The active Mac source contains the 536/600-byte
dynamic-subclass guard. The Windows `refs/AdrenoMetal` reference still has the older 536-byte guard and is not the
source of the delivered plugin. Mac readbacks currently end at run 38.

Host delivery check: `Get-FileHash build/share/blueline-run39-kernel31-gpu.7z -Algorithm SHA256` matches
`db8c1bf080346373256fa7346774ca5f2a7554c878099493ba9f66de004282b7` (20,644,720 bytes).
`7z t build/share/blueline-run39-kernel31-gpu.7z` passes for all four files. These are archive checks, not new phone tests.

Next: preserve this boot's log by returning to fastboot and temporarily booting the existing TWRP image. In the folder
containing adb and the run-39 bundle's `blueline-dump.ps1`, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\blueline-dump.ps1
```

Send `blueline-readback.7z` (or `.zip`). No `-Data` is needed initially. Decode with
`python scripts/blueline-log.py <path-to-blueline-log.bin>`; inspect boot ID, uptime/round progression, write errors,
`[plugin]` markers/reports, `[client]`, `[opens]`, `[row ...]`, and `[gop] gpu completions` before choosing a code change.
Do not boot XNU again before capture: its logger replaces the previous run's header and message buffer. No new kernel,
image, partition write, seed change, commit or push was made during this resume.

### Run 39 readback (2026-10-28): Metal device created; backboardd aborts on OS18 selector

Owner supplied `C:\Users\user\Desktop\blueline-readback.7z` (110,469,982 bytes). `7z t` passes; SHA-256
`9c94c3d67b7b77684a659fe6da13e11e225eca4b3542442b5aab0a8e0d7a8ee2`. Contents: `blueline-log.bin` (33,554,432 bytes),
`data-bitmap.bin` (2,621,440 bytes), `data-pages.bin` (591,642,624 bytes). Archive and extracted Data files are preserved
on the Mac; no writes were made to the phone.

**UFS and liveness.** Command:

```powershell
python scripts/blueline-log.py C:\Users\user\AppData\Local\Temp\opencode\blueline-readback\blueline-log.bin
```

Decoded files: `build/blueline-logs/blueline-191aeed9/{header.txt,latest.txt,slots.txt,msgbuf.txt}`. Header: SHLOG001 v4,
boot ID `0x191aeed9`, 674 rounds/slots, uptime 1266 s, state writing, zero writer errors. The 509 retained slots cover
rounds 165-673 (uptime 248-1266 s), following the photo's r163. This confirms XNU logging continued well beyond the
photographed checkpoint, reaching about 21 minutes of logger uptime. The final pre-read `ph 19 r gpu+2018` marker is
not evidence of a whole-machine hang. Earlier ring slots were overwritten normally; the header has no full image hash.

**GPU and Metal.** The retained driver trace includes ME_INIT and CP_MEM_WRITE fence success, ZAP init/mem-setup/auth,
native publication and Metal registration. Plugin markers repeatedly reach `6800` (entered), `6801` (Turnip found),
`6814` (cache candidate 4), `6802` (Metal device made), `6803` (returned to discovery). Final diagnostic counters: 34
opens / 34 grants / 34 closes, 4728 calls, zero busy/bad/refused; opener is backboardd. The read-back plugin CDHash
`5bbc1760f7e4b59d1f66c2fc9a7c4446b2e2ab86` matches the prepared run-39 plugin. This is beyond run 38's `_MTLDevice`
base-size refusal, but not proof of a completed render.

**Display.** All 509 retained slots show `[display] a 0 1 1 1 1 0 0 0 0 0 0`: display stage 10, successful init/probe/start,
presenter initialized; zero swap validations/refusals, refused GPU offers, transfers and retirements. `[gop] gpu
completions` is always 0. Both plugin report strings are blank; no `[client]` or `[row ...]` telemetry appears.

**Crash cause.** The reconstructed Data contains seven backboardd `.ips` reports, all on boot session
`B46D0CCE-38CF-4E4F-B61F-3BAD53A00B73`, iOS 18.7.11 / 22H384, at reported uptimes 150, 310, 330, 370, 450, 610 and 930 s.
Every report has `SIGABRT` / `NSInvalidArgumentException`, unrecognized selector `setResponsibleTaskIDs:count:`.
Exception stacks run through `CA::OGL::MetalContext::start_command_buffer` → `start_render_encoder` → drawing.
launchd logs confirm successive backboardd crashes and exponential respawn throttling (latest recorded delay 637 s),
and record SpringBoard killed by backboardd. The active Mac `NativeBridgeMetalFrontend.mm` at AdrenoMetal `d49f74c`
has no implementation of that selector. Leading diagnosis: a missing OS18 command-buffer API in the custom frontend.
Confirm the receiver and exact 22H384 selector signature before implementing; no frontend fix has been made yet.

**Storage/readback integrity.** Bitmap has 144444 pages in 50 runs; its expected page bytes match the supplied file.
Windows/Mac hashes match: bitmap `ad15b8d2231cb18e7968caaf26b9069cc7234ed6268ac53e7cfaf50da155b19a`,
pages `41f2190ccd0a317d9d5d8624b6fb045ff0db297a30b522d53b8308cbadc3c0e3`. Reconstruction uses the existing seed,
SHA-256 `e3b1ea02c0baea7e1c085b6734178b23966e998c26b256cb1423416a5d1835fd`:

```sh
python3 ~/blueline/data-readback/blueline-data-rebuild.py \
  ~/blueline/ios18-tools/blueline-data/blueline-data.dbr \
  --bitmap ~/blueline/data-readback/run39-attached-191aeed9/data-bitmap.bin \
  --pages ~/blueline/data-readback/run39-attached-191aeed9/data-pages.bin \
  -o ~/blueline/data-readback/run39-attached-191aeed9/run39-data.img
hdiutil attach -readonly -nomount -imagekey diskimage-class=CRawDiskImage \
  ~/blueline/data-readback/run39-attached-191aeed9/run39-data.img
mount -t apfs -o rdonly,nobrowse,noowners /dev/disk19s1 \
  ~/blueline/data-readback/run39-attached-191aeed9/mnt
```

The 80 GiB reconstructed image mounts read-only with expected Data UUID `789552B2-C2BA-476F-BE80-99D2ECA02A7C`.
Disk numbers above are this attachment only; verify before reuse. Crash reports are under
`mnt/mobile/Library/Logs/CrashReporter/backboardd*.ips`; launchd logs under `mnt/log/com.apple.xpc.launchd/`.
Archive copy: `~/blueline/data-readback/run39-readback.7z`. The image is left mounted read-only for the next agent.
Unified logs have not yet been decoded for this readback; the direct `.ips` exception reason already identifies the gap.

**Handoff.** Owner asked to hand off to Claude. Next: inspect the 22H384 `setResponsibleTaskIDs:count:` contract and actual
receiver, implement/test the smallest correct frontend compatibility change, then build/check the plugin before another
phone candidate. Preserve root, Data seed/UUID/geometry, `cpus=1 sheng-smp=0`, GPU-only presentation and no colour bars.
No runtime code, kernel/plugin build, new image, phone access, commit or push during this analysis; documentation only.
Handoff validation: `git diff --check` passes (line-ending warnings only);
`python refs/UniXNU-main/scripts/porting-record.py check --device .` passes all 13 structured areas.

### Run 39 readback re-checked (Claude) and prepared run 40 (2026-10-28)

The owner asked whether the Codex run 39 analysis can be trusted. It was re-checked from the same archive (SHA-256
9c94c3d6…8ee2), with an independent /var rebuild (`readback.sh 39`, boot B46D0CCE) and the unified log decoded.
- Confirmed: UFS header 674 rounds, 1266 s, 0 write errors. Plugin markers 6800/6801/6814/6802/6803 repeat; backboardd made
  34 opens and 34 closes. Zero swaps, transfers and GPU completions. Seven backboardd `.ips` reports, all from this boot
  session, all with the same plugin image (UUID fe9e07b2…), all SIGABRT on `setResponsibleTaskIDs:count:` from
  `CA::OGL::MetalContext::start_command_buffer`.
- Added: the unified log names the receiver the `.ips` redacts: `-[NBCommandBuffer setResponsibleTaskIDs:count:]`. The 22H384
  QuartzCore disassembly agrees (`ctx+2992` is `[queue commandBuffer…]`; x2 is an int32 vector, x3 = (end-begin)>>2; the
  result is unused). backboardd crashed more than seven times: once the kernel's corpse limit was reached ("Corpse failure, too
  many"), later aborts left no report. launchd throttled it.
- Corrected: SpringBoard was not killed by backboardd. The SIGABRTs are backboardd's own abort (sender = its own pid), and
  SpringBoard exits normally (exit 0 or 255) after losing backboardd. liveactivitiesd SIGTRAPs and bluetoothd/fairplayd throttling
  are pre-existing and unrelated. `native-metal: no device answer` lines (gpuCoreCount and others) are informational.
Verdict: the diagnosis and the next step are right. Two details were off (SpringBoard, crash count), and neither changes the fix.

Run 40 (kernel 31 unchanged): Mac UniXNU drivers/AdrenoMetal 6289622 (local) implements `setResponsibleTaskIDs:count:` on
NBCommandBuffer as a no-op. NBObject now absorbs any unknown selector an MTL…SPI protocol declares with a void result, through
methodSignatureForSelector/forwardInvocation, so respondsToSelector: is unchanged and a result-returning selector still raises.
Each absorbed selector is journalled ("I") and noted once. A host check of the mechanism passes 5/5. MoltenVK is not
installed, so the frontend's full host test (check-native-metal-frontend-host.py) was not run. A 22H384 copy of
audit-metal-api-surface.py ran (build/gpu-analysis/metal-api-surface-22H384.json on the Mac). There is no iOS 17 baseline to diff
against; QuartzCore references a few public selectors the bridge source lacks (texture/state gpuResourceID, makeAliasable,
setTileBuffer, and others), which iOS 17 apparently never exercised. Plugin CDHashes 2740c40f…c2d3, adrenoprobed 5afd02a2…084a,
Turnip 5c058b63…44f5. The trust bundle is 3af40826c758edee9b7019499e61c09a1ec390dd7411cc330cf84e5a2d72879c (allowed in Mac
UniXNU d99aa94). The GPU addon has 9 files, 12,173,576 bytes. Payload 738b4b40…53bf1, boot image
`build/boot/blueline-unixnu-mu-simplefb-xnu31b-gpu.img` c9ef18de14bc2bb3060b02a14c87b0cf449d566836ae92472a80420e60ac9b97,
archive `build/share/blueline-run40-kernel31b-gpu.7z` 479d0ec0f85c57778400ad2196af27f291cb772b5c748b1bf170764f7f2850a9.
Tools added on the Mac: `~/blueline/ios18-tools/objc_class_size.py` and `dsc_disasm.py` (read the 22H384 cache in place).

### Run 40 result (2026-10-28): first GPU frame; PreBoard dims the panel for idle (no input to wake it)

The owner saw the status text vanish at about 2 minutes, then a black screen. The readback archive has SHA-256 fd069b06…5007.
- UFS log: 241 rounds, 398 s, 0 write errors. The A630 brings up exactly as in run 38. backboardd opened the accelerator twice,
  without crashing, so the run 39 abort is fixed. The scanout client marked 6720/6730/6731: one frame offered, copied by the GPU
  and completed (`[gop] gpu completions 1`). Six command buffers were committed and completed, with 2.3 s mean and 3.8 s max
  commit to complete. Pipelines: 72 made, 42 s total, 14.6 s max. The VkPipeline cache missed 119 times (first boot with the
  Library/Caches directory). The plugin's calls stopped at about 154 s (log uptime). After that only the monitor heartbeat
  (0x6A38) repeats.
- Runtime errors (telemetry P rows): a metallib with `LibraryKind(1)` (Apple file type 1, which metal2vulkan
  accepts only as 0 executable or 2 dynamic), and one render pipeline refused as `InvalidDescriptor(ColorAttachment { index: 2 })`.
  The second is from metal2vulkan's link checks (graphics.rs: an input attachment or a fragment output at color 2 that the
  descriptor's attachment 2 does not match). The descriptor is not logged yet.
- Unified log (boot 7BD088AD, /var rebuilt with `readback.sh 40`): SpringBoard runs, blocked for the alternate system app
  PreBoard (PBASimpleLockScreenView).
  - The first PreBoard (pid 109) force-undimmed and then dimmed for idle. Its CoreAnimation transition to the display-off state
    did not finish within 15 s (rendering too slow), so BacklightServices' watchdog aborted it (exit domain
    backlightservices code 20).
  - The second PreBoard (pid 171) force-undimmed at mach 164.1 s. It logged "dimming for idle" 30 s later (BLS timestamps
    3150384403 → 3734936979 at 19.2 MHz).
  - backboardd then set the display off (`IOMFBDisplay::set_enabled=0`, about 154 s log uptime) and never turned it back on.
  - The board has no touch or kernel buttons, so nothing can wake it.
  - liveactivitiesd SIGTRAP loops, as before.
Conclusion: the black screen is the idle dim, not a GPU fault. Only one frame reached the panel before it went dark, and
the photo shows it as black. Its content is unknown: the PreBoard lock view is mostly black, and the failed pipeline may have
dropped its text. The two readback Data images on the Mac (runs 39/40) are detached.

### Run 41 prepared (2026-10-28): kernel 32 with the power and volume-down keys

The owner chose a kernel power-button driver over a keep-awake workaround.
- `package/BluelineButtons.cpp` reads the PM8998 PON real-time status (SID 0, peripheral 0x08, register 0x10: bit 0 KPDPWR,
  bit 1 RESIN) every 20 ms. It uses EXT_READL on the SDM845 PMIC arbiter v5 observer channel (core 0xc440000, obsrvr
  0xe600000, cnfg 0xc40a000; APID from the core map table, preferring the EE-0 owner). This is the same read-only access as
  BluelineKeyInputDxe and pixel3-ios 0104-blueline-keys.patch. Nothing in the PMIC is written. Volume up is not read.
- The keys reach iPadOS through ShengHidInput 5f3a37c (`NativeHidInput`, report 2: Consumer Power and Volume Decrement)
  on the original IOHIDFamily-2115 and IOHIDEventDriver kexts of the 22H384 kernelcache. The startup is sheng's
  HidInputProbe sequence: load the kexts, wait for IOHIDResource, register the class late, then init/attach/start. It runs
  on a thread launched from `native_legacy_display_late_probe`. `blueline-keys=0` turns it off.
- The 22H384 IOHIDDevice ABI was found with `~/blueline/ios18-tools/hid18_abi.py` on the Mac and matches 21H461: 207 vtable
  slots, pure slot 180, the same 18 IOService overrides, 0xd0 bytes. Kernelcache addresses: vtable 0xfffffff006d64688, C2
  0xfffffff005c7ae5c, D2 0xfffffff005c7ae80, gMetaClass 0xfffffff007be2960 (prelinked at -0x1000000).
  `package/build-blueline-hid.py` checks each one structurally against the kernelcache. It also checks the 52 bindings in
  the built kernel (both kernel 31 and kernel 32) and that the compiled vtable owns only slots 0 1 7 14 86 87 111 171 173 180.
- The UFS log gains a `[keys]` line: stage (6 = polling with the HID device up), arbiter version, PON APID, kext loads, key
  changes, mask, read errors, HID start and report counts.
- Run 40's metal2vulkan errors (metallib LibraryKind(1) from SafariSharedUI, `path_blit` ColorAttachment 2) come from the
  bridge's warm-up pass (prime.log, pipelines.log in backboardd's m2v cache). That pass compiles system metallibs with
  guessed descriptors. Every pipeline QuartzCore asked for compiled. The warm-up is marked done (`prime-done`) and the
  VkPipeline cache is populated, so it should not repeat. No bridge change.
- Build fixes along the way: the Mac copy of the scripts needs LF line endings. NativeHidInput's weak
  `native_touch_set_sink` needs a definition in a static kernel link, so a no-op is provided.
Kernel 32 9c6a027c…e39f; payload b58818f0…2ffc (plugin, trust bundle, Data ramdisk and arguments as run 40); boot image
`build/boot/blueline-unixnu-mu-simplefb-xnu32-keys.img` ef7f0c2e3051a96ed71a2ede9cc6d8644037fe4dc9186ff6fe267cafd12742ee;
archive `build/share/blueline-run41-kernel32-keys.7z` 9d03949ba5db2d5de6b7d2fbf3b33a5a888ceba699de8e78c03bd32e89c4d991.
Not yet run on the phone.

### Run 41 result (2026-10-28): keys reach the HID stack, backboardd ignores them; run 42 prepared

The owner pressed the keys and nothing happened. Readback archive 65d2bedb…c4b8; UFS log: 148 rounds, 212 s, 0 write errors.
- `[keys] stage 6 arbiter 50000000 apid 172 kexts 0/0 changes 38 mask a errors 0 | hid started 1 reports 38 last 0`.
  The PON reader, the HID kexts, the late class registration and the device start all work. Every press produced an
  accepted report (handleReport 0), and that happens only after IOHIDEventDriver attached. The kernel log has
  `BluelineButtons: keys 0x2 / 0` pairs at mach 211–217 s.
- The /var unified log (boot C0AF7B56) has no key event in backboardd. Its persisted entries end before the presses: the dim was
  at mach 161 s, and the owner rebooted with the keys, so logd's buffer was lost. It shows the same PreBoard idle dim as run 40.
- Cause (most likely): the device's only collection was a keyboard (Generic Desktop 1/6) with the Consumer bits inside it,
  so backboardd took the events as an external keyboard's keys. The 22H384 IOHIDEventDriver personalities are keyboard
  1/6 and 1/7, "HID Buttons" Telephony 0x0b/1, a sensor, and a FIFO-only consumer-control display. ios-sweet's working
  buttons were a dedicated consumer service.
- Rendering: the warm-up was skipped and the VkPipeline cache hit 13/13 (40 ms). Pipeline creation still took 41.6 s in
  total (max 18 s), and frames took 3.5 s mean from commit to complete. The time is in the metal2vulkan translation, which is the
  next performance target.

Run 42 (kernel 33 c1add81b…7190): `build-blueline-hid.py` now replaces ShengHidInput's descriptor with one Telephony/Phone
application collection holding the same report 2 (Consumer Menu, Power, Volume Increment, Volume Decrement). The device is
marked Built-In, and there is no keyboard collection. Payload 081b0474…f0b6, boot image
`build/boot/blueline-unixnu-mu-simplefb-xnu33-buttons.img` fe631cf47de8ff67ba11e4eceb23be0db7cb333124e2846660610a9de02efe09,
archive `build/share/blueline-run42-kernel33-buttons.7z` 1d9ab5e8fb87664ca2b735c79fe50e47b7226edae1de8e2f9570603a36705aa2.
The test note asks for a 1-minute wait after the last press, so logd writes its log.

### Run 42 result (2026-10-28): the display stalls after the first GPU frame; run 43 prepared

The owner saw no display change. Archive 41b4252d…17a5; UFS log: 137 rounds, 190 s, 0 errors.
- `[keys]` 58 changes, 58 accepted reports (Telephony device), as in run 41. There is again no backboardd entry for the presses:
  logd had not persisted its last minutes (boot F4A96E62; persisted entries end soon after the dim at mach 234 s, while the
  presses were at mach 327 s). Userland stayed alive (spawns at 344 s), so this is not a system sleep.
- **Main finding:** `[display] a 0 1 1 1 1 12 0 0 2 0 1`. backboardd's swaps keep being validated (18), but after the first
  frame only 2 transfer events and 1 retirement happen, and no GPU offer is refused. From the first frame on, every
  `IOMobileFramebufferSwapWait` takes 5.00 s and the kernel logs `IOMFB: swap_wait_gated: CommandWake didn't happen even after
  5 seconds … cancelling swaps`. The queue kick returns silently before offering the next frame. That explains the black screen
  (the lock screen is never redrawn), most of the 3.5 s per frame, and possibly the dead power key (undim needs a
  CoreAnimation display transition, which waits on these swaps).
- Rendering: 9 command buffers, 0.9 s mean commit-to-complete; 13 pipelines took 33.8 s.

Owner goals added (2026-10-28): at most 20% loss against native GPU performance (60 fps), and 8 cores at full clock instead of
one core.

Run 43 (kernel 34): build-blueline-display.py adds reason codes to every silent exit of NativeLegacyDisplay's kickOnce,
retireSwap and finishGpuSwap. BluelineDisplayProbe counts them (`blueline_kick_state`), and the UFS log has a `[kick]` line:
1 transfer off, 2 still transferring, 3 powered off, 4 no mapping, 5 not primary, 6 no pending swap, 7 not validated, 8 no
IOSurface, 9 offered to the GPU, 10 retirement could not activate, 11 GPU completion without a frame. Two payloads of the same kernel:
one core, and eight cores (`cpus=8 sheng-smp=1`) as the first eight-core test since run 28's `not_in_kdp` assert.

### Run 28 eight-core failure re-examined (2026-10-28)

Owner asked why the earlier eight-core attempt failed. From the xnu-11417 source in our tree:
- `not_in_kdp` is a global cleared only while one core is inside a debugger operation (`handle_debugger_trap`; userland
  stackshots take this path). `pmap_tt1_deallocate` asserts it (DEVELOPMENT kernel), so the run 28 panic means another core
  was still running `pmap_destroy` during a stackshot.
- The halt is `DebuggerXCallEnter` (osfmk/arm/model_dep.c):
  - The stackshot core sends SIGPdebug through `cpu_signal` to every other core. That is ArmCpu::signalCPU, an SGI 0 via
    ICC_SGI1R_EL1; the affinity encoding is right for SDM845's 0x000..0x700.
  - It waits `debug_ack_timeout`, the machine timeout "debug-ack" = 240000 timebase ticks. That is 10 ms on Apple's 24 MHz,
    and 12.5 ms on SDM845's 19.2 MHz.
  - On timeout it does not panic. It tries `ml_dbgwrap_halt_cpu`, which the SDM845 does not have, logs "Debugger
    synchronization timed out", and the stackshot proceeds with that core still running.
- So at least one core did not take its SGI within 12.5 ms. Either it was slow to wake (deep idle, a long
  interrupts-masked section) or the SGI never arrived. run 29 showed bluetoothd requesting a stackshot every few seconds,
  which makes the race frequent.
- Run 43's eight-core payload sets `ml-timeout-debug-ack=19200000` (1 s), and kernel 35 logs per-core timer and SGI counts
  (`[cpu]`). A slow core then acknowledges in time. A core whose SGI count stays 0 never receives IPIs, which would also
  break scheduling, TLB shootdowns and the other halts.
- Still unchecked: ArmCpu's idle/wake path for the Gold cluster, and whether QHEE traps ICC_SGI1R_EL1.

Run 43 packaged: kernel 35 3714f369…e92a (kernel 34 plus the `[cpu]` line). Payloads: one core 0be2abc6…0ff9, eight cores
181edf99…c302 (`cpus=8 sheng-smp=1 ml-timeout-debug-ack=19200000`). Boot images:
`blueline-unixnu-mu-simplefb-xnu35-1cpu.img` 8eb20eb3ed7441f58962bac4c96a65ceac75091920266cf54cbd9a87bbfd97ba,
`blueline-unixnu-mu-simplefb-xnu35-8cpu.img` 1b46d94e3cdc3fb2d03d51e0c23f08edf59cb310b9b037f8960c343c8060488b.
Archive `build/share/blueline-run43-kernel35.7z` 0128807ac055ca4269e2f61e094e9768e57d0b9cc0d7ec13f0e107d59de6b9af.

### Run 43 eight-core result and a process mistake (2026-10-28)

Eight-core image (kernel 35, `ml-timeout-debug-ack=19200000`): **the first visible iPadOS UI.** PreBoard's "swipe up to upgrade"
was drawn through the GPU path. There was no touch (no driver yet). It went black after about 30 s (PreBoard idle dim), and
power did not wake it. Later it panicked with `thread wait info count mismatch while capturing snapshots for task …,
expected 20, found 19 @kern_stackshot.c:4971`, `exec=SystemCommandsSuggestionsPlugin`, `last=AppleKeyStore`. Frames, from atos on the
kernel 35 unstripped image: `kdp_stackshot_record_task` ← `stackshot_cpu_work_on_queue` ← `stackshot_cpu_do_work` ←
`kdp_stackshot_kcdata_format` ← `do_stackshot` ← `handle_debugger_trap` ← `kdp_trap` ← `DebuggerTrapWithState`. It is the same
class of failure as run 28: a thread changed state during a stackshot, so a core was still running, even with a 1 s ack
timeout. That points to a core that never takes the debugger SGI, rather than a slow one. The eight-core dump's `[cpu]` line
will show which core it is.

**Mistake (Claude, recorded at the owner's request).** `refs/pixel3-ios` (erikkunz, iOS 16 on this same phone) already has:
- power and volume up/down (0104, 0109);
- touch (0105, 0113, 0114, 0118, 0121: ST FTM5 over bit-banged I2C, LDO19 over RPMh, interrupt-driven, edge gestures);
- all 8 cores (0101, 0103, 0112, 0116, 0117), cpufreq (0106), a battery (0119/0120) and USB (0108/0122).

Its HID path is ios-sweet's SweetHID (refs/ios-flame 0058): the buttons are a dedicated IOHIDEventService with primary usage
Consumer/ConsumerControl. For run 41 I ported only its PMIC register reads (0104) and designed a new HID path on the iOS 17
ShengHidInput. That cost runs 41 and 42. I also wrongly stated volume up could not be read (0109 reads it) and did not port
touch at all. For SMP, 0101/0106/0112/0117 had been ported earlier, but when run 28 failed nobody went back to the reference's
LOG and its SMP diagnostics (0103/0116). The owner's rule, now in the global CLAUDE.md: port the reference's whole working path
first, and go back to it before debugging alone.

Next, from the reference:
1. Buttons and volume up as its dedicated consumer-control service, plus touch (FTM5 chain), adapted to the prelinked
   IOHIDFamily of 22H384.
2. Its SMP diagnostics (0116) to find the core that misses the debugger SGI.

### Run 44 prepared (2026-10-28): keys and touch ported from pixel3-ios

The owner asked to port the iOS 16 work instead of reinventing it.
- Keys: package/BluelineButtons.cpp now follows pixel3-ios blueline_keys.c (0104 + 0109). It reads power and volume down from
  PON RT_STS, and volume up from PM8998 GPIO 6 STATUS1 after setting its pull-up once (DIG_PULL_CTL 0xc542 = 0, only if the apps
  EE owns the APID).
- Touch: package/BluelineTouch.cpp is blueline_touch.c (0105, 0113, 0114, 0121) ported unchanged in its bus, power and FTM5
  parts: bit-banged I2C on GPIO 27/28 with GPIO 136 high, the LDO19 3.3 V vote over RPMh through the cmd-db, and the FTM5
  reset/interrupt/sense-on and event decoding. It polls (0113's mode). The 0121 interrupt mode needs an SPI mask that ArmCpu
  does not have yet.
- Services: as ios-sweet's SweetHID, which the reference uses (refs/ios-flame 0058): a built-in Consumer Control buttons
  device, and a separate built-in Digitizer/Touch Screen device with an unused Consumer Control collection. On 22H384 only
  the "Peppy Touch Display" IOHIDEventDriver personality (Consumer Control, Transport FIFO) matches such devices, so both
  report FIFO. The edge-gesture wrapper is disabled because its IOHIDFamily-2031 vtable slots are unchecked for 2115.
- New UFS log line `[touch]`: stage, chip id, RPMh error, start bits, events, errors, down mask, reports.
- Also pushed (owner's request): UniXNU-sdm845-common to f048c75, and UniXNU-blueline with feeds.toml pinned to it.
Kernel 36 1ae7729c…12be. Payloads: eight cores 18e67e10…6e56, one core aa3ba12f…f610. Boot images:
`blueline-unixnu-mu-simplefb-xnu36-8cpu.img` 9386f000a6dd38388faa220c5c9372b375ecae6f554332666fda65fe16cbb2bb,
`blueline-unixnu-mu-simplefb-xnu36-1cpu.img` 07f03eea20b5365cf971900343e3caef77ffe7949e1571ffc5fb9e4c810d4024.
Run 44 replaces run 43 (it contains everything run 43 had).

### Run 43 eight-core dump and run 44 result; run 45 prepared (2026-10-28)

Owner, run 44 (eight cores): SpringBoard loads (it did before too), no touch or very slow, power does not respond, black after a
while; no panic. No run 44 dump yet. The Desktop's `blueline-readback.7z` (a9ff8c84…8325, 13:17, before the run 44 images) is
run 43's eight-core boot 2d95bdab: 297 rounds, 512 s, 0 write errors, no panic in that boot. Decoded into
build/blueline-logs/blueline-2d95bdab.
- **The display queue stops for good at about 98 s.** By 92 s: 6 GPU offers (`[kick]` reason 9), 6 GPU completions, 6
  retirements, but 7 transfers, all with status 0. From then on every kick exits with reason 2 (still transferring), 33 times by
  512 s, while backboardd's swaps keep being validated (36). The 7th transfer is a blank swap (no surface; NativeLegacyDisplay
  fills the GOP black). It then calls `finishTransfer(request)` without force, and `retireSwap` retires only with boot-arg
  `offline-legacy-retire=1` or `offline-legacy-cpu-present=1`. Blueline had neither, so `transferred` stayed set. sheng,
  fajita and dipper all pass `offline-legacy-cpu-present=1` ("retire each swap after its copy"). Only retireSwap reads it.
- **The power key works in iOS.** Each press is followed within 0.1 s by backboardd's `request_power_change … powerstate 1`
  (mach 214.8, 361.4 and 531.1 s, after display-off at 179.2, 289.7 and 436.0 s). The screen stayed black because the queue
  was stuck, not because the key was ignored. `[keys]` 26 changes, 26 accepted reports (run 43's Telephony device).
- `[cpu]`: all eight cores take timer interrupts (55k–95k each), but `sgi` is 0 on every core and `ipi sent` stays 8 for the
  whole boot. Not explained yet (a counting gap, or the IPIs go another way). sheng passes `armcpu-spi-ipi=1`; not tried here.

Run 45: kernel 36 unchanged, board.toml adds `offline-legacy-cpu-present=1`. Eight-core payload only
(`~/blueline/run45-build.sh`) abb5b49a…4e2c, boot image `blueline-unixnu-mu-simplefb-xnu36b-8cpu.img`
8cc8a352437af88049ed6ec8088ee183ac1ee3438786271cf861578576f87a00 (fd 3e744b3c…), folder build/share/run45.
Expected: the picture keeps updating after a blank frame, and power brings it back. Touch is unknown until a dump shows `[touch]`.
Run 45 was not tested: the owner chose software rendering first (below).

### Run 46 prepared (2026-10-28): software rendering

Owner: switch to software rendering, fix the other issues (input and the rest), then come back to the GPU.
References: fajita (OnePlus 6T, also SDM845; refs/UniXNU-fajita board.toml and drivers/NativeMobileDisplay) renders iOS 17
on the CPU through NativeLegacyDisplay's CPU copy, and UniXNU's QEMU target documents that without a Metal device QuartzCore
uses its CPU renderer.
- `package/build-blueline-display.py --cpu-copy` (archive script: `CPU_COPY=1`) keeps the CPU copy and adds fajita's two copy
  speedups as boot arguments: `fajita-fb-wc=1` maps the GOP framebuffer write-combined (fajita: the uncached copy was ~59% of
  all CPU samples), `fajita-fb-src-cached=1` reads the surface through a cacheable mapping after a clean+invalidate.
  Without the option the build is unchanged (CPU copy removed, GPU candidate).
- board.toml: `sheng-fb-gpu=1`, `blueline-gpu=2` and `sheng-gpu-addon=1` removed (no A630 bring-up, no Metal accelerator, no
  plugin mount), `fajita-fb-wc=1` and `fajita-fb-src-cached=1` added, `offline-legacy-cpu-present=1` kept. The payload has no
  `--trust-cache`/`--gpu-addon`.
- Unchecked: whether 22H384's backboardd runs without any Metal device (QEMU's no-Metal evidence is iPadOS 17).
Kernel 37 18bf104d…6d04 (Mac `~/blueline/run46-build.sh`, kernel37-build.sh), eight-core payload f6f68572…cdfca
(`cpus=8 sheng-smp=1 ml-timeout-debug-ack=19200000`, a build-time edit; board.toml keeps cpus=1), boot image
`blueline-unixnu-mu-simplefb-xnu37-swrender-8cpu.img` 236a2d7bff67ef9c96648159f4d61e843718a24f3a5b78387d37c4ad61c6326a,
folder build/share/run46 (TEST-RUN46.txt). Look for in the dump: `[display]` transfers and retirements rising together
with `[kick]` not stuck on reason 2, `[touch]` stage/events/reports, `[keys]`, and backboardd crash reports in /var.

### Run 46 result and run 46b (2026-10-28): launchd killed, no trust cache in the payload

Panic screen: `unexpected SIGKILL of launchd with reason -- namespace 9 code 0x8 description none`, `exec=dyld`. Namespace 9
is OS_REASON_EXEC and code 8 is EXEC_EXIT_REASON_SECURITY_POLICY: the exec of launchd (through dyld) was refused by the code
signing policy. Not the earlier AMFI 16 KiB stack-check bug (runs 25-27; fixed since kernel 20). Cause: my run 46 payload
dropped `--trust-cache` along with the Metal plugin. The GPU builds' blueline-trust.bundle is the plugin's owner module plus
the two original 22H384 Apple modules (package/make-blueline-trust-bundle.py), so without it the payload had no trust
cache at all, and no platform binary could run.
Run 46b: the same kernel 37, payload rebuilt with `--trust-cache ~/blueline/ios18-tools/trust/22H384-static-system.bundle`
(the original Apple bundle, 26ed8386…7ce0, accepted by the profile; payload.json records it). Payload 034379f9…581d, boot
image `blueline-unixnu-mu-simplefb-xnu37b-swrender-8cpu.img` b4b1485d010e7c1ec8cc0a215a0b613223b36821596e5cab71c12c99ff35799a,
archive build/share/blueline-run46b-kernel37-swrender.7z 200fa5c4…60f4e3.
Rule: a payload always needs a trust cache; without the GPU plugin, use the original 22H384 bundle.

### Run 46b result (2026-10-28): eight-core stackshot panic; IPI fix written, not built

Photo: `thread wait info count mismatch … expected 6, found 7 @kern_stackshot.c:4971`, exec=UARPUpdaterServiceDFU (run 43's panic again).
Root cause (from run 43's `[cpu]` line and xnu source): ArmCpu sends IPIs as SGI 0, and no SGI 0 ever arrived (sgi 0 on every
core, even each core's own start-up self-IPI). XNU clears a core's SIGPdisabled only when that core takes its first IPI
(osfmk/arm/cpu_common.c cpu_signal_handler_internal), and cpu_signal() returns without sending while it is set. So every IPI
after boot was dropped (`ipi sent` stayed 8, the 8 self-IPIs): no reschedule IPIs, no TLB/xcall IPIs, and the stackshot's
SIGPdebug never halted the other cores. Reference: pixel3-ios/ios-sweet use SGI 1 (`SWEET_IPI_SGI 1`, refs/ios-flame 0004/0018)
and verified it on all 8 cores of this phone (refs/pixel3-ios docs/LOG.md "SGI1 OK"); their GIC setup otherwise matches ArmCpu's.
Written, NOT built or tested:
- UniXNU-sdm845-common 698dc78 (local, not pushed): ArmCpu boot arg `armcpu-ipi-sgi=N` (default 0) for send, self-IPI and dispatch.
- board.toml: `armcpu-ipi-sgi=1`.
- package/BluelineLog.c: runs ArmCpu's armcpu_sgi_selfprobe once at log start (which SGIs 0..15 become pending as Group 1/0
  on that core), resends one IPI after it, and logs `[sgi] probe …`. BluelineLog.c is built by build-blueline-gpu.py, so
  the archive script has to run again.

### Run 47 prepared (2026-10-28): eight cores with IPIs on SGI 1 (software rendering)

The owner set the GPU aside for now and continues with software rendering. Built the handoff's pending IPI fix
(UniXNU-sdm845-common 698dc78, pushed; feeds.toml pinned to it): ArmCpu's `armcpu-ipi-sgi` (board: `armcpu-ipi-sgi=1`) moves
the kernel's IPIs from SGI 0, which reached no core in runs 43/46b, to SGI 1, as the iOS 16 Pixel 3 port does (ios-sweet
SWEET_IPI_SGI, verified there on all eight cores). The UFS log gains the `[sgi]` self-probe line.
Kernel 38 6e159c60…16dc (CPU-copy archives, `CPU_COPY=1`; Mac ~/blueline/run47-build.sh). Eight-core payload 6bc09639…2f21 (trust
cache 26ed8386…, no GPU arguments, `offline-legacy-cpu-present=1`, fajita copy speedups). Boot image
`blueline-unixnu-mu-simplefb-xnu38-swrender-8cpu.img` e7f2c854f7b72d5a769c1d513efce800ea79cae2855fbe5ee50c7372c8bdeee9,
archive `build/share/blueline-run47-kernel38-swrender.7z` 7f1bc50c859a1c3ea0e93303271ed9ec8f8a0c7212eb136042e53963bd1195d2.
What to read in its dump: `[cpu]` SGI counts above 0 on every core and `ipi sent` rising; `[sgi]` g1 bit 1 set. If SGI 1 also fails, the
next step is `armcpu-spi-ipi=1` (sheng's software-pended SPI IPIs).

### Report after run 47 (2026-10-28): handoff screen, about one minute, watchdog reset to Android

Owner: "kernel boots, then phone reboot without kernel panic message". Presumed run 47/kernel 38 from the latest
handoff; exact filename/hash remains unconfirmed. Owner then clarified the last screen is "hand off to kernel"
("purple screen if enabled color bars"), stuck there for about a minute, then reboot into Android. No visible panic.
This is a silent-reset observation, not proof of a particular panic or reset source. The Desktop readback still has timestamp
13:17 and belongs to the older run-43 evidence; it must not be treated as a new run-47 capture.

Owner subsequently collected Android's boot reasons after the reset:

```text
adb shell getprop ro.boot.bootreason
watchdog
adb shell getprop sys.boot.reason
watchdog
```

This establishes Android's watchdog-reset classification, not the particular watchdog source or the failure that
caused it to expire. The two properties may derive from the same boot-reason record, not independent measurements.
An unseen panic/hang before watchdog expiry remains possible. No new kernel/UFS capture accompanies these outputs.

Last confirmed visible stage is loader handoff. The current board disables colour bars; the colour analogy is not new
evidence that the magenta XNU entry probe actually ran in this attempt. Per the UniXNU handoff/green-bar guide, stale
handoff output alone does not identify where execution stopped. Android now reports watchdog reset, consistent with
the roughly one-minute interval, but neither the timing nor properties locate the preceding stall. Do not disable or
extend the watchdog, change watchdog policy or enable colour/reset probes merely to mask or reproduce this failure.

Source inspection identifies a new regression candidate: `blueline_log_start()` calls `armcpu_sgi_selfprobe()` before
`kernel_thread_start(writer, ...)`. The pinned SoC implementation sends all 16 SGIs, consumes acknowledgements, and
falls back to `ICC_SGI0R_EL1`, `ICC_HPPIR0_EL1`, `ICC_IAR0_EL1` and `ICC_EOIR0_EL1` (Group 0). The earlier run-11-to-16
correction records a silent reset from another Group-0 register access (`ICC_IGRPEN0_EL1`). This makes the new active
probe a plausible cause, but does not establish which access reset this run. If it fails before the writer starts,
the UFS window may still describe an older boot. No probe or reset was reproduced during this inspection.
The probe is reached from the display's early IOKit registration hook, not the loader. Its removal is a precaution;
there is no current evidence yet that this boot reached that hook.

Local precautionary correction in `package/BluelineLog.c`: remove the automatic probe, replacement self-IPI and obsolete
`[sgi] probe` output. Keep the passive `[cpu]` timer/SGI/IPI counters unchanged. Leave `armcpu-ipi-sgi=1`, CPU count,
software-rendering settings, trust cache, root image, Data seed/UUID and store geometry unchanged. No SoC source or
feed pin changed. Do not switch to SPI IPIs on the basis of an unobserved `[sgi]` probe result.

This source correction is not yet a rebuilt kernel/payload/boot image and has no phone qualification. A successor must
rebuild the archive containing `BluelineLog.c` (not just the payload), with `CPU_COPY=1`, then kernel and payload while
preserving run 47's Apple trust cache. After the corrected boot, inspect passive `[cpu]` counts and the latest UFS
boot ID/rounds before drawing a CPU/IPI conclusion. Capture the current log via TWRP before another XNU boot if possible;
the owner has now supplied the timing, last screen and Android destination. A fresh dump and exact image identity are
still needed; an old UFS header would not prove a particular pre-logger stall location.

Host checks: `git diff --check` passes (line-ending warnings only); the porting-record validator passes all 13 areas.
A source-invariant check confirms no active probe/replacement-self-IPI reference, preserved passive CPU counters/writer
startup and unchanged `armcpu-ipi-sgi=1`. On the Mac, compiled a temporary copy of BluelineLog.c using
`Sdm845Gpu/build-native.py`'s `command()` with the resolved `bsd_init.o.json` XNU C recipe, then audited the ARM64/iOS
object and ran `xcrun nm -u`: no probe/selftest imports; all three passive CPU counter imports remain. Artifacts and
full command: `~/blueline/run47-logfix-check-7cem2lfz/{compile.log,check-report.json,BluelineLog.o}`.
Source SHA-256 `5fd6a213ce2a432911f914679b565360268ac0dd0d23116fab34214130207fd4`; object
`fa3b69a5e80c8813ebab8b0e216eea19907249a739330ab6150f37a80658a039`. This is an isolated object check, not a rebuilt
archive/kernel or a phone result. The active Mac source checkout was not overwritten.

### Watchdog-attempt readback (2026-10-28): new capture, likely earlier boot's retained log

Owner supplied a new `C:\Users\user\Desktop\blueline-readback.7z` (file timestamp 15:57:11, 42,537 bytes). `7z t` passes;
archive SHA-256 `68e57b1b5a4e236147822c3f5018fdf09ee5927ba313bc2f12cc74fbf7252013`. It contains only the 33,554,432-byte
UFS window `blueline-log.bin`, SHA-256 `611eba671e2304fbddb2156e0394dfaadcc853017f34fb5212cd6fa47cb07271`.
Extracted separately under `%LOCALAPPDATA%\Temp\opencode\readback-watchdog-68e57b1b`; previous decoded evidence is untouched.

Decode command:

```powershell
python scripts/blueline-log.py C:\Users\user\AppData\Local\Temp\opencode\readback-watchdog-68e57b1b\blueline-log.bin --out build/readback/watchdog-68e57b1b
```

Decoded directory: `build/readback/watchdog-68e57b1b/blueline-1ce64c8b/`. Header: SHLOG001 v4, boot ID `0x1ce64c8b`,
549 rounds/slots, logger uptime 1097 s, state writing, zero write errors. Ring retains slots 40-548 (80-1097 s), 509
snapshots. This is about 18 minutes of logging, not the roughly one-minute attempt described by the owner.

Captured-boot observations, **not assigned to run 47**:
- No `[sgi] probe` line anywhere. `[cpu] ipi sent` stays 8, all eight SGI counters stay zero.
- Timer counters on cores 1-5 continue changing through the last slot; cores 0/6 last change at logger time 82 s and
  core 7 at 142 s. Counter stasis alone does not prove a core is dead.
- Data state 2, zero write errors, writes reach 22221 (last counter change at 140 s).
- `[display] a 0 1 1 1 0 0 0 0 0 0 0`: display started, no presenter (expected with GPU handoff disabled), no swap
  validations/transfers/retirements. GPU stage 0, plugin opens/calls 0, GPU completions 0.
- Touch event/report counters increase from 257/81 to 266/84; keys record two reports. These are retained-boot
  observations, not qualification of input on the current watchdog attempt.
- The 128 KiB msgbuf has userland process activity and late button messages at kernel time ~1110 s. No captured panic
  or watchdog-expiry message identifies the latest reset; absence is not proof that no panic occurred.

Checked the actual Mac run-47 binary, not just current source. Kernel `kernel-k38.macho` SHA-256
`6e159c60633cbbe4926efce05bdd10395b592f749f8bd8fd2a6a448e78e616dc` matches its build record. `xcrun nm -g` resolves
`_armcpu_sgi_selfprobe`, `_armcpu_ipi_selftest` and `_blueline_log_start`; strings includes `[sgi] probe ...`.
`xcrun llvm-objdump -d --start-address=0xfffffff007a56a5c --stop-address=0xfffffff007a56c50 <kernel-k38.macho>` confirms
the probe/selftest calls precede setting `sgi_probed` and creating the writer thread. Thus a normal logger run from
this binary should include the probe line; the captured boot does not match that expectation or the reported duration.

Leading interpretation: this is a fresh recovery capture of a previous software-render boot's log, left intact because
the watchdog attempt did not commit a new UFS header. A different boot image is another possibility. The header stores
no kernel/image hash, so this cannot be named definitively run 46b or used to conclude run 47's SGI-1 fix failed. It also
does not prove the failing attempt reached the Group-0 probe; the stall remains unlocated.

Next evidence: exact `.img` filename and SHA-256 used for the watchdog attempt (prepared run-47 boot-image hash is
`e7f2c854f7b72d5a769c1d513efce800ea79cae2855fbe5ee50c7372c8bdeee9`). Do not rerun the probe-bearing image or repeat
the same recovery capture just to reproduce this result. The local logger-probe removal remains object-checked only;
no corrected full archive/kernel/payload/image has been prepared. No further runtime edits, phone accesses, commit or
push during this analysis. Preserve software rendering, original Apple trust cache, root and Data seed/geometry.

### Run 47 identity confirmed; two repeat attempts and exact delta from run 46b (2026-10-28)

Owner's Windows `certutil -hashfile` output for
`C:\Users\DELL\Desktop\p3\blueline-unixnu-mu-simplefb-xnu38-swrender-8cpu.img` is
`e7f2c854f7b72d5a769c1d513efce800ea79cae2855fbe5ee50c7372c8bdeee9`, matching prepared run 47. Owner confirms using
this latest image twice, with the same handoff-screen wait (~one minute), no visible panic and Android watchdog reset.
This establishes a repeatable reported regression from the prior software-render image; do not keep treating wrong
image selection as the leading explanation or ask for the same hash/timing again. It does not assign the retained
18-minute log to either failing attempt.

Comparison commands: device `git diff 8419b8e 2883f9d -- board.toml package/BluelineLog.c feeds.toml`; SoC
`git diff f048c75 698dc78 -- drivers/ArmCpu/ArmCpu.cpp`; read-only diffs of Mac `kernel37-build.sh`/`kernel38-build.sh`
and `run46-build.sh`/`run47-build.sh` (the current run46 script includes the run46b trust-cache correction).

| Setting/path | Run 46b / kernel 37 | Run 47 / kernel 38 |
| --- | --- | --- |
| IPI selection | SGI 0 | `armcpu-ipi-sgi=1`; send/selftest and dispatch use SGI 1 |
| Logger startup | allocate buffer/lock, start writer | allocate, call active SGI probe and replacement selftest, then start writer |
| SGI-probe snapshot | absent | `[sgi] probe` emitted after calls return |
| CPU count | eight-core payload | eight-core payload (unchanged) |
| Rendering | CPU_COPY=1, GPU addon disabled | same software-render configuration |
| Trust cache | original 22H384 Apple bundle | same bundle, retained explicitly |
| Root/Data and bars | stable seed/geometry; no bars | unchanged |
| Observed result | visible stackshot panic | repeatable silent watchdog reset, no fresh log identified |

The device source diff is one new IPI argument, logger probe invocation/output, SoC pin and progress documentation.
The SoC diff changes IPI selection; the probe function already existed but the board newly invoked it. Kernel build
scripts differ only in labels and kernel output names; run47 rebuilds archives/kernel and packages the new kernel.
No change disabling the panic screen appears in these compared sources/scripts.

The new active probe is the most specific risky regression candidate: Group-0 access and interrupt acknowledgement
happen before the logger thread exists. SGI-1 delivery also changes earlier kernel behavior, so the two effects are not
yet separated on hardware. Lack of panic output cannot establish that no panic was attempted; an earlier trap/deadlock
or failure in panic rendering could prevent it before watchdog expiry. Do not assume an intentional panic-policy change.
Next controlled comparison is the local automatic-probe-removal patch with SGI 1 and all other inputs unchanged; it has
passed isolated object compilation but no corrected full image has been built. No new runtime edit, build, commit or push
for this comparison.

### Run 48 prepared (2026-10-28): kernel 39, logger probe removed, software rendering

Owner requested investigation/fix, compilation and a download link. Removed the active SGI/Group-0 probe and its
replacement selftest from `package/BluelineLog.c` startup; passive `[cpu]` timer/SGI/IPI counters remain. This is the
only intentional runtime source change relative to run 47. SGI 1, eight cores, software rendering, original Apple
trust cache, root, packed Data seed/UUID/geometry, firmware, panic screen and no-bars policy remain unchanged.
This removes a hazardous regression candidate; it is not yet evidence that the hardware watchdog issue is resolved.

Provenance: device `2883f9d775029ca80b3078203620b3c498e3e294` plus the recorded uncommitted logger/documentation edits;
Mac UniXNU `d99aa9438db21a72fd8fe0b880fec3b9dd3efdba`. The Mac SoC source directory has no Git metadata; its ArmCpu.cpp
SHA-256 `b4e9d3f0ccb13f600231908666436873287da072625d5351ed2a5056d55e1bde` matches the local pinned SoC `698dc78`.
Patched logger LF SHA-256 `5fd6a213ce2a432911f914679b565360268ac0dd0d23116fab34214130207fd4`. Backed up the previous
Mac logger and board under `~/blueline/run48-probe-fix-backup-20261028/`; verified baseline hashes before syncing only
the logger. Earlier images/sources and unrelated Mac changes were not discarded.

Build recipe: ignored local `build/run48/run48-build.sh`, staged as `~/blueline/run48-build.sh`. It verifies the seed,
trust cache, board and previous kernel hashes, backs up prebuild outputs/reports, then runs:

```sh
CPU_COPY=1 bash ../UniXNU-blueline/package/build-blueline-kernel-archives.sh \
  out/blueline/ipados-18.7.11-22H384/blueline/tree \
  out/blueline/ipados-18.7.11-22H384/blueline/kernel/kernel-k38.macho
./unixnu defconfig blueline_defconfig -D ../UniXNU-blueline
./unixnu status --json
./unixnu build kernel
# Save kernel.macho as kernel-k39.macho; temporarily apply run-47's eight-core payload override.
./unixnu build payload --kernel out/blueline/ipados-18.7.11-22H384/blueline/kernel/kernel-k39.macho \
  --uefi ~/blueline/uefi-font \
  --data-ramdisk ~/blueline/ios18-tools/blueline-data/blueline-data.dbr \
  --trust-cache ~/blueline/ios18-tools/trust/22H384-static-system.bundle
```

Mac logs/reports: `~/blueline/run48-artifacts/` (`archives39.log`, `kernel39.log`, `payload39.log`, inputs/status,
payload/kernel reports); old outputs in `prebuild/`. The script restores board.toml byte-for-byte on exit, verified
SHA-256 `663743b521490897c3e27313661d4fb86f1d8cf4bc39a982c4f1369fb53fd074`. GPU work stays deferred.

Host results:
- ARM64/iOS/-Werror archive builds pass; 22H384 accelerator/display/HID binding checks pass, CPU copy explicitly kept.
- Kernel and prelink succeed. Comparing the old/new merged Adreno archive's 42 object members (43 entries including
  the symbol index), **only BluelineLog.o differs**. Passive counter imports remain. The final kernel's
  `_blueline_log_start` disassembly calls `kernel_thread_start` without calling either probe helper. Audit and disassembly:
  Mac `run48-artifacts/audit/`; copied object-hash audit to local `build/run48/audit-report.json`.
- Payload kernel arguments, trust-cache hash, Data seed hash, GPU-disabled state, panic-screen setting and payload size
  match run 47. Extracted actual FAT kernel/ADT/trust/Data files match report hashes, not just staging-path claims.
- Parsed old/new embedded ADTs: eight measured CPU affinities/topology and all board/memory properties are identical;
  only `/chosen/random-seed` differs. Local comparison: `build/run48/adt-comparison.json`.
- Original Apple trust SHA-256 `26ed8386ab1984442d2e65679bc4ed4544ba8a71dcfccdecaf39e5ceb6ef7ce0`; packed Data seed
  `e3b1ea02c0baea7e1c085b6734178b23966e998c26b256cb1423416a5d1835fd` unchanged. No root or Data-store write on phone.
- Payload patching keeps panic text (`--panic-screen --no-bars --no-reset`); no diagnostic reset or watchdog-policy change.

Windows packaging command (also in ignored `build/run48/package-run48.ps1`):

```powershell
python scripts/package-blueline-boot.py --fd build/firmware/blueline-mu-simplefb-debug.fd --ramdisk build/run48/payload-k39-8cpu.img -o build/boot/blueline-unixnu-mu-simplefb-xnu39-swrender-8cpu-probeoff.img
```

The packer verifies header v1/text_offset 0x80000 and round-trips shim/firmware, appended DTB and complete payload.
Firmware SHA-256 remains `3e744b3cbc5de99f44d6ab85a1c433f557804173b8bdb504a9abfaf33210b7f2`. Run-47 image hash remains
`e7f2c854f7b72d5a769c1d513efce800ea79cae2855fbe5ee50c7372c8bdeee9`.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Mac `kernel-k39.macho` | 56926208 | `70e4dccb6ef0ad05f79997f702d234e01da29f73a0be7a0aa64ebbd49cfa249b` |
| Panic-patched FAT `kernel.macho` | 56926208 | `70b4a4d2f679054b34e22367fd88ce07c2765382341698db0e11bef86ea9374c` |
| `build/run48/payload-k39-8cpu.img` | 83886080 | `a4769b3bcd345cbf1771cb543b8a3fb683fb7287f33d9e2f678b53e1b9ede409` |
| `build/boot/blueline-unixnu-mu-simplefb-xnu39-swrender-8cpu-probeoff.img` | 84951040 | `54d3ab2130ab31f341f505f658d7cdc1638ca43ac9e7e5666dc73aeb8317d481` |
| `build/share/blueline-run48-kernel39-swrender-probeoff.7z` | 8593810 | `87708811a7cde90635bdeab4db30fda74480955ca07b9cb0f7bf79b2366c548d` |

Delivery bundle has four files: image, `blueline-dump.ps1`, `TEST-RUN48.txt`, `SHA256SUMS`. Manifest and `7z t` pass.
Complete localhost and external-share downloads match the archive hash. Private share address is kept only in ignored
HANDOFF.md and the owner-facing response, not tracked documentation. No new server configuration, commit or push.

Phone results are **pending**: boot/watchdog recovery and eight-core IPI delivery are not yet qualified; storage inputs
are preserved and host-checked; display/input need the run-48 capture; GPU is deliberately off. Owner should boot once,
then capture UFS through TWRP before another XNU boot. Read passive `[cpu]` counts and a new boot ID; absence of the old
`[sgi] probe` line is intentional. If a watchdog recurs, collect Android boot reasons before recovery and retain the
last screen/timing; do not re-enable the probe or keep repeating the failing candidate.

### Run 48 panic and run 49 prepared (2026-10-28): SPI IPIs

Run 48 (kernel 39, the other agent's build without the SGI self-probe) got past run 47's watchdog reset. It then panicked with a
kernel data abort, a write to address 0 (esr 0x96000045, far 0), in `exec=privacyaccountingd`. Symbolized against kernel 39:
`stackshot_thread_turnstileinfo` (kern_stackshot.c:6127) ← `kdp_stackshot_record_task` ← `stackshot_cpu_work_on_queue` ←
`stackshot_cpu_do_work` ← `kdp_stackshot_kcdata_format` ← `do_stackshot` ← `handle_debugger_trap` ← `kdp_trap` ←
`DebuggerTrapWithState`. The first pass over the task counted no turnstile-waiting thread (no buffer reserved, array NULL),
the second pass found one and wrote through NULL (x1 = 0). So a core was still running during the stackshot, the same race as
runs 28, 43 and 46b: SGI 1 did not halt every core either. Neither pixel3-ios nor ios-flame handles stackshots (older kernel).
Run 49 (kernel 40): sheng's software-pended SPI IPIs (`armcpu-spi-ipi=1`). ArmCpu (UniXNU-sdm845-common, pushed; feeds.toml
pinned) gains `armcpu-spi-ipi-base`, because the default INTID 1008 is not implemented on a 960-line Qualcomm GIC
(ios-sweet: GICD_TYPER 0x007B041D). Blueline uses 732 (SPIs 700..707; the stock Pixel 3 DT uses SPIs up to 608, the PDC up to
636). The UFS log gains `[gic]` (the boot core's GIC readback, including GICD_TYPER at index 3).
Kernel 40 7a88ff38…3182, eight-core payload d92b627d…568b (trust 26ed8386…), boot image
`blueline-unixnu-mu-simplefb-xnu40-swrender-8cpu.img` f4bb1f4cc3b78909a1debcaf7fa06a1a803b05c811554f3343e6270540a8deb3, archive
`build/share/blueline-run49-kernel40-swrender.7z` b536f353e68f18fe63ad0341912a0c940bd0d35f265e4e43def45b2c54c67a8f
(Mac ~/blueline/run49-build.sh).
In its dump, check `[gic]` GICD_TYPER (ITLinesNumber = bits 4:0), `[cpu]` IPI counts above 0 on all cores, and whether the stackshot panic is gone.

### Run 49 result (2026-10-28): SPI IPIs, CPU 6 never acknowledged; text-mode boot menu prepared

Photo only (no dump yet). After `DARWINBOOT: hand-off to the kernel` the phone panicked with
`DebuggerXCallEnter>Debugger synch pending on cpu 6 @model_dep.c:1182`, `exec=SiriSuggestionsBookkeepingService der=3121`.
Symbolized against kernel 40: `DebuggerXCallEnter` (return address lands in `DebuggerXCallReturn`) ← `handle_debugger_trap` ←
`kdp_trap` ← `DebuggerTrapWithState` ← `stackshot_trap` ← `kern_stack_snapshot_internal` ← `stack_snapshot_with_config` ←
`unix_syscall`. A stackshot passes `proceed_on_sync_failure = FALSE` (kern_stackshot.c:1153), so any core that does not
acknowledge SIGPdebug within `debug_ack_timeout` (1 s here, `ml-timeout-debug-ack=19200000`) panics with this message;
`timeout_cpu` is the last such core in CPU order, so CPU 6 failed (CPUs below it unknown, CPU 7 acknowledged or was the caller).
SPI IPIs at INTID 732..739 therefore did not reach CPU 6.

Correction to the run 48 reading: because of that rule, run 48 (SGI 1) would have panicked the same way if a core had ignored
SGI 1. It did not, so in run 48 every core probably acknowledged (or was counted offline), and "a core still running" was
likely wrong. xnu-11417 runs stackshots on all halted cores in parallel (`stackshot_cpu_do_work`); the run 48 NULL write
(and the earlier "thread wait info count mismatch") may come from that parallel path instead. The kernel has a boot-arg
switch for it: `stackshot_single_thread=1` (kern_stackshot.c:135, used at :2702). Not built yet: needs the owner's go-ahead.

Text-mode boot menu (owner request): two graphical layers were drawn before the kernel.
- Mu's BootManagerLib draws `BootLogo.bmp` (`DisplayBootGraphic (BG_SYSTEM_LOGO)`). uefi-blueline `2e7bd73b` maps
  `BootGraphicsLib` to Mu's `BootGraphicsLibNull` in blueline.dsc (BdsDxe, MsBootPolicy and MassStorage all link it).
  That commit also records the working-tree edits every firmware since run 20 was built from (no DarwinBoot band,
  MemoryMapLib RAM partition, BluelineBootMemoryDxe clipping). FD `build/firmware/blueline-mu-simplefb-debug.fd`
  b9da0c0163a48e7985a23dc1a92211c5e67a5e5c27f651a14e50984ab7e72afe (previous FD kept as `-pre-textmode.fd`, 3e744b3c…b7f2).
- UniXNU's menu already supports `[ui] console = "text"` (UniXNULib Config.c/Ui.c: the menu then uses ConOut, not GOP).
  The payload builder never wrote `[ui]`; Mac UniXNU `45968dd` (local, not upstream) passes `[payload.ui]` through, and
  board.toml sets `console = "text"`, `logo = false`. Takes effect on the next payload build.

### Run 50 prepared (2026-10-28): text-mode boot menu, SGI 1, single-thread stackshots

Kernel 40 unchanged. Eight-core payload (Mac ~/blueline/run50-build.sh, same cpus=8/sheng-smp=1/ml-timeout-debug-ack edit as
run 49) with board.toml back on SGI 1 (`armcpu-spi-ipi` and `armcpu-spi-ipi-base` removed) plus `stackshot_single_thread=1`
(string present in kernel 40), and `[payload.ui] console = "text"`, `logo = false` (verified inside the image: unixnu.toml has
the `[ui]` table). Payload `payload-k40-run50.img` 8ec84bc4078d07ac0967602f804e4fb9324a6e3ec99ea31c799f4c205ee024a2 (trust
26ed8386…). Firmware FD b9da0c01…2afe (uefi-blueline 2e7bd73b, BootGraphicsLibNull). Boot image
`build/boot/blueline-unixnu-mu-textmode-xnu40-swrender-8cpu.img` 0b38cf679659116380651124656ec82d240965ef0980c9178bb5301e2365d5cb,
archive `build/share/blueline-run50-kernel40-textmode.7z` 8ae92298315b435f9950ec07d299124906cbaeddc55a3b015df6d30511e65f04
(download verified). Expect: text-only boot menu; if the stackshot panic is gone, the parallel stackshot path was the cause.

### Run 50 result (2026-10-28): two panics at once; proof that a core runs during the stackshot

Photo only. Two panic screens drawn over each other (two cores panicking together), `exec=PreBoard der=1789 last=AppleKeyStore`.
- Layer 1 (stackshot core): `thread wait info count mismatch ... @kern_stackshot.c:4971`, frames
  `handle_debugger_trap` / `kdp_trap` / `DebuggerTrapWithState` as in runs 28/43/46b. So `stackshot_single_thread=1` did not
  help: the parallel stackshot path was not the cause.
- Layer 2 (another core): `brk #0xbffc` (XNU_HARD_TRAP_ASSERT_FAILURE) at pc 0xfffffff007ab5894, descriptor
  `pmap_tt1_deallocate.__desc`: `assert(not_in_kdp)` failed in `pmap_tt1_deallocate` (inlined into `pmap_destroy_internal`)
  ← `pmap_destroy` ← `vm_map_destroy` ← `posix_spawn` ← `unix_syscall`. `not_in_kdp == 0` means the debugger/stackshot
  was active while this core was still running ordinary kernel code (tearing down a failed spawn's address space).
This is direct evidence that a core is not halted during the stackshot even with SGI 1 and no "Debugger synch pending"
panic, so the run 49 note ("in run 48 every core probably acknowledged") is wrong: a core can stay running without the
sync check noticing. Candidates: a running core that DebuggerXCallEnter never signals (missing or shared
`CpuDataEntries`/cpu number, `max_cpu` below 7, a core counted offline) or one that acknowledges and keeps running.
Text-mode boot menu: no comment yet from the owner.

### Reference check (pixel3-ios / ios-sweet) after run 50: the missing self-IPI check

Mechanism, from xnu-11417 source:
- `cpu_signal()` returns KERN_FAILURE, before sending anything, while the target's `cpu_signal` has `SIGPdisabled`
  (osfmk/arm/cpu_common.c:455-461). Every core starts with it set (arm64/cpu.c:800); it is cleared only when that core
  takes its first IPI (`cpu_signal_handler_internal(FALSE)`, cpu_common.c:583-588).
- `DebuggerXCallEnter` counts and waits only for cores whose `cpu_signal()` succeeded; with a stackshot's
  `proceed_on_sync_failure = FALSE` a failure is skipped without a log line (model_dep.c:1088-1094). A core that never
  cleared `SIGPdisabled` therefore keeps running through every stackshot: run 50's `assert(not_in_kdp)` core and the
  count mismatch. No "synch pending" panic, because nothing waited for it.
- On AppleARMCPU kernels (ours) XNU does not wait for the self-IPI (`wait_for_cpu_signal = false`, kern/startup.c:977),
  so a core whose self-IPI is lost boots and schedules normally.
- Run 43's `[cpu]` line fits exactly: `ipi sent` stayed 8 for the whole boot (the eight start-up self-IPIs from
  `ArmCpu::initCPU`, `armcpu-self-ipi=1`) and `sgi` was 0 on every core. No self-IPI landed, no core cleared
  `SIGPdisabled`, and the kernel never sent another IPI because `cpu_signal()` failed for every target. No dump assigned
  to an SGI 1 run exists yet (run 47's readback was a retained earlier boot), so whether SGI 1 self-IPIs land is open.
  Run 49 (SPI): CPU 6 had cleared it (its `cpu_signal()` succeeded) but then did not acknowledge SIGPdebug.

What the reference does that we do not: ios-sweet `SweetCPU::startAll` (refs/ios-flame 0065-sweet-smp-psci.patch, which
pixel3-ios builds on) starts each core and then waits up to 5 s for `sweet_cpu_signal_is_enabled(i)` (an XNU accessor:
`!(CpuDataEntries[i].cpu_data_vaddr->cpu_signal & SIGPdisabled)`), logging "came alive; SGI1 OK" or panicking
"startup timeout (reset/MMU/GIC/SGI)". pixel3-ios 0116 then logs per core: IPIs sent, the `cpu_signal` flags
(comment: "SIGPdisabled = 0x80000000 makes cpu_signal() drop every IPI to it"), processor state/priority/thread, and the
GICR SGI pending/enable bits and WAKER. Our ArmCpu (UniXNU-sdm845-common) sends the self-IPI but never checks it landed.
The pixel3-ios LOG has no stackshot notes; its probe showed SGI 1 with XNU's encoding reaching every core in 4-7 us.

Proposed port (not started): the ios-sweet accessor as a device XNU patch, ArmCpu waiting for each core's
`SIGPdisabled` to clear after `initCPU` (logging the core that fails instead of running on silently), and the 0116
per-core fields in the UFS log's `[cpu]` line. The run 50 dump's `[cpu]` line would already show whether SGI 1
self-IPIs land.

### Run 51 prepared (2026-10-28): kernel 41, SMP start-up check ported

Owner: "Port it, build 51". `package/BluelineSmpDiag.c` (device e983d98), compiled with XNU's osfmk recipe
(`cpu_common.o.json`) into libBluelineGpu.a, so no XNU patch and no clean kernel rebuild:
- Port of ios-sweet `SweetCPU::startAll` (refs/ios-flame 0065): a thread started from `blueline_log_start` waits up to 5 s per
  core for Init+Started, then up to 5 s for `SIGPdisabled` to clear (`sweet_cpu_signal_is_enabled`). New, not in the reference
  (which panics): a core that never clears it is recorded and sent up to 20 IPIs from the checking core through
  `PE_cpu_signal` (the kernel's own path; `blueline-smp-kick=0` turns it off). Result per core in `blueline_smp_check`:
  ms (self-IPI landed), 10000+n (cleared after n kicks), -2 (never), -3 (core not started). Also printed to msgbuf as
  `BluelineSmp: check ... | sig ...`.
- Port of pixel3-ios 0116: the UFS log gains `[smp]` (check, cpu_signal flags, processor state/priority per core) and `[gicr]`
  (GICR ISPENDR0/ISENABLER0/WAKER per core, frames at 0x17a60000 matched by GICR_TYPER affinity). Reads only.
Kernel 41 bcf09cbca474223e16bf0b10cafb10c1f4f86f4361e25a4465103c076a10447c (Mac ~/blueline/run51-build.sh; archives built
against kernel 40). Payload `payload-k41-run51.img` 756a541924f59e1caeb4cf3cecc2c19df674e0b63503c4c04014acbd19200c15 (run 50
settings: SGI 1, stackshot_single_thread=1, text-mode menu; trust 26ed8386…). FD b9da0c01…2afe. Boot image
`build/boot/blueline-unixnu-mu-textmode-xnu41-swrender-8cpu.img` 747ac80bd586b18cc2d6e1e50144851f6e2b480f964487e153b28742447b6f54,
archive `build/share/blueline-run51-kernel41-smpcheck.7z` bdfdc62e90d072e6ecb3dc71378a41b66ff1fafae0604e87b1544b59d8009086
(download verified). Read in the dump: `[smp] check` per core, `sig` (0x80000000 = still disabled), `[gicr]` pending bit 1
(SGI 1 pending but never taken) vs not pending (never generated or routed).

### Run 51 result (2026-10-28): the same running-core assert, one panic screen

Photo only. `pmap.c:3577 Assertion failed: not_in_kdp` at pc 0xfffffff007ab6188 (kernel 41: `pmap_destroy_internal.cold.3`,
the inlined `pmap_tt1_deallocate`) ← `pmap_destroy` ← `vm_map_destroy` ← `posix_spawn` ← `unix_syscall`;
`exec=BTAudioHALPlugin der=0 last=IOAcceleratorES`. Identical to run 50's second layer: a core ran ordinary kernel code
while a stackshot was in progress. This time only that core's panic screen is drawn (no count-mismatch layer).
Not yet known whether the start-up check ran before the panic, or what it found: `der=0` suggests an early panic, possibly
before the UFS log wrote its first slot. XNU's halt path checked again (`DebuggerXCallEnter`/`DebuggerXCall`, normal; the
halted cores also do stackshot work), so the skipped-core explanation still stands until the dump says otherwise.
Next: the run 51 dump (`[smp]`, `[gicr]`, msgbuf `BluelineSmp: check`). If the log is empty, show the per-core check on the
panic screen in the next run.
