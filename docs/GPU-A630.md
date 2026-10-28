# Adreno 630 integration

This is a UniXNU port: reuse its NativeAdreno v2 client/VM/memory and
AdrenoMetal frontend, with SDM845 platform operations and A6 command framing.
The owner requires the next phone test to contain GPU rendering, one CPU until
SpringBoard, persistent Data and no colour bars. No CPU-rendered display test
is planned. GopFramebuffer is the scanout target; the 22H384 ABI derivation is
in DISPLAY-22H384.md.

## Current checkpoint (2026-10-28)

Run 38 reaches GMU/HFI startup, CP/fence/ZAP checks, native publication and Metal
accelerator registration. Its readback identifies the userspace plugin's refusal
of iPadOS 18's 600-byte `_MTLDevice` base. Run 39 / kernel 31 includes the fix
(Mac AdrenoMetal `d49f74c`) plus plugin markers/reports/telemetry in each UFS-log slot.
Run 39 readback now confirms Metal-device creation and return to discovery
(markers `6802/6803`); its plugin CDHash matches the prepared build. The UFS logger
reaches 1,266 seconds with zero writer errors. No swap, transfer, retirement or
GPU completion is observed. Seven backboardd reports repeat an unrecognized
`setResponsibleTaskIDs:count:` selector in QuartzCore's `start_command_buffer`.
Lead diagnosis: missing OS18 command-buffer API in the custom frontend. Verify
the receiver and exact 22H384 signature before implementing.

Board sources: `package/BluelineGpu.cpp`, `BluelineDisplayProbe.cpp`, `BluelineLog.c`,
and `build-blueline-{gpu,accelerator,display}.py`. Shared SDM845 operations remain
in `UniXNU-sdm845-common/kits/Sdm845Gpu`. The OS18 build and plugin source of record
are on the Mac, not the older `refs/AdrenoMetal` reference checkout.

Registration is not proof of a userspace Metal device or a presented frame.
`ph 19` is kit GpuReady and `gpu+2018` is CP_RB_RPTR. The status is emitted before
the read, once per phase/window transition, so it does not establish a stalled bus.
`disp` counts accepted/refused swap validations, not rendered frames.

Next: verify `setResponsibleTaskIDs:count:` against 22H384 and the receiving
AdrenoMetal object, then implement/test the smallest correct compatibility change
in the active Mac source. Preserve root, Data seed/UUID and overlay geometry.
Commands, hashes, crash details and read-only Data artifacts are in PROGRESS.md,
"Run 39 readback". No runtime fix or new candidate has been made during this
analysis; userspace rendering, scanout and performance remain unverified.

The implementation survey and takeover plan below are historical 2026-10-27
checkpoints; their pending integration tasks were subsequently carried out in
runs 30-39 as recorded in PROGRESS.md.

## References and inputs

- NWylynko/ios-flame `745c4341af86089efb0df851fa8a27fb8face624`, fetched
  2026-10-27: Metal on A640 / iOS 16.7.16. The final A6 source was reconstructed
  from patches 0095 through 0122 under ignored build/gpu-reference, preserving
  the reference checkout. Its fixed physical pools, A640 clock/PDC windows,
  permissive security patches and standalone display are not board inputs.
- UniXNU Adreno740 `4e0314df8ad29d52f6fa3f9383a540adcd618b87` supplies the
  reusable client/VM/memory/ring code. The A740 platform and BR/BV command
  sequence do not apply to an A630. AdrenoMetal uses Turnip and metal2vulkan;
  the A740 replay prefix must be disabled or regenerated for A630.
- Linux v6.12 SDM845 clock, A6xx GMU/HFI/GPU descriptions are recorded with
  per-file SHA-256 in build/gpu-reference/linux-v6.12/manifest.json.
- Pixel factory SP1A.210812.016.C2 vendor.img: firmware/adreno630.json pins
  the sparse image and all seven extracted firmware files. Run
  `python scripts/prepare-blueline-gpu.py --verify-only` to verify local copies.

## What ios-flame contributes

The working Metal path already derives from UniXNU: tools/build-turnip-ios.sh
uses Mesa commit 85fda3b3 plus UniXNU's Darwin backend patches, and
tools/build-m2v-bridge.sh uses UniXNU's NativeMetal bridge over metal2vulkan
720ef1ce. SweetMetal2.mm loads that frontend through the accelerator port.
This confirms the same overall architecture is viable on A6xx; it does not
qualify A630 hardware or the iPadOS 18 private framework ABI.

| Topic | Integration here |
| --- | --- |
| Kernel/user transport | Flame's "v2 client" header still advertises wire ABI 1 and 16 IBs. UniXNU advertises ABI 2 and 128 IBs. Build our Turnip against our exact header; do not mix binaries. |
| A6 command/fence path | Reuse the single-CP, CCU invalidation and CACHE_FLUSH_TS behavior in the existing UniXNU ring; keep its VM, imported-page ownership and bounded fault handling. |
| Platform startup | A640 has different clocks, PDC windows, GMU firmware and modern HFI. Use stock A630 firmware, SDM845 operations and legacy HFI. |
| Surface cache maintenance | Flame supplies a real EL0 dc civac flush for imported IOSurface pages. Qualify our cache-sync path and EL0 permissions before using cached imports; do not assume libSystem's flush performs GPU maintenance. |
| Padded IOSurface writers | Flame aliases padded writers and reduces allocation blocks to 8 MiB. Current UniXNU already has AliasWrites plus a tile-store bounds check (bridge patch 0016); preserve that stronger bounds handling rather than replacing it with blanket aliasing. |
| Driver discovery and scanout | Keep UniXNU AGXAccelerator, AdrenoMetal and GopFramebuffer handoff. Qualify their original 22H384 bindings and remove OS17 diagnostic writes from the candidate. |
| Shader/pipeline persistence | Use the existing writable persistent Data for caches/logs. GPU addon staging must not change the seed identity and discard the current overlay. |

Reference README reports system UI/app Metal rendering at about 20–26 fps on
A640. Its listed exceptions include Today-view widgets, SEP and hardware video
decode; those results and performance are not claims for this port.

## Board facts

The stock boot.img DTB 0 is .cache/firmware/stock-dtb/00.dtb. GPU registers
are 0x05000000/0x40000; GPUCC 0x05090000/0x9000; GMU 0x0506a000/0x30000;
GPU SMMU 0x05040000/0x10000. GPU SPI 300, HFI SPI 304, GMU SPI 305.
GPU nonsecure SID 0, secure SID 2/1; GMU user/kernel SIDs 4/5. Attach only
owned nonsecure streams and available banks/SMR slots; do not borrow flame's
0x401 route or mask SID 0 together with secure SID 1.

The RSCC subwindow is GMU+0x23000. A630 PDC register/sequence subwindows are
0x0b280000 and 0x0b480000 (Linux SDM845, within stock PDC 0x0b200000/0x300000).
Flame's A640 0x0b290000/0x0b490000 are different. Stock reserved ZAP memory is
0x8c415000/0x2000. cmd-db pointer register is 0x0c3f000c/8; the actual bounded
database, version and ARC indices must be obtained before requesting rails.

Stock chip ID is 0x06030001, highest bank bit 15, UBWC mode 2, minimum access
32 bytes. Enabled GPU levels are 257, 342, 414, 520, 596, 675, 710 MHz; use
the stock initial 257 MHz level for bring-up. GMU has 200/500 MHz levels.
A630 GMEM starts at GPU VA 0x100000, so the A740 SQE mapping at that same VA
must move for A630. Keep allocations inside XNU's owned memory; no foreign
hardcoded physical pool. These DT facts do not prove live clock or GPU state.

## Historical core implementation (2026-10-27, before integration)

- AdrenoFirmware adds explicit legacy v1 transport: two queues, response
  queue type 0xa04, dword indices and unpadded messages. Modern v2 remains
  three queues with four-word padding. A630 startup is INIT, FW_VERSION,
  two-word performance entries, bandwidth, TEST; no modern ACD/IFPC path.
  Compiled AArch64 emulation checks pass, including bad messages, wraparound,
  firmware status 14, queue-full refusal and poisoned uncertain exchanges.
- UniXNU-sdm845-common/kits/Sdm845Gpu supplies bounded clock/CX, flat ITCM
  loading/readback, SDM845 PDC/RSCC and rail/SPTP/HFI startup. It uses GCC
  DDRSS 0x44038, PDC GPU resource 0x30080 and HFI init register 0x5186*4.
  It now verifies stable GPLL0 configuration against the board's independently
  qualified reference before writing, requests legacy GPU_SET/DCVS and programs
  A630 64-bit addressing, HWCG, VBIF, GMEM/UCHE, UBWC and CP protection.
  988 host ASan/UBSan checks pass, including clock and DCVS failures. The
  independently captured fixture trace matches 105 Linux A630 HWCG writes,
  32 protection spans and 14 hardware policies. Interrupts stay masked.
  FinishBoot requires the caller's real CP/ZAP/fence proof; GPU_SET stays held.
  These are host fixtures; actual reference clock and live state are unqualified.
- NativeAdrenoRing adds explicit A6 framing to the existing UniXNU class:
  single CP, UCHE/CCU invalidation, optional context-entry SRAM clear,
  CACHE_FLUSH_TS with IRQ, no A7 thread packets. Failed publication retains
  resources; stuck/backward clocks cannot wait forever. Actual ring host tests
  pass for both A6 and the unchanged A7 profile under ASan/UBSan.
- NativeAdrenoCpInit adds legacy ME_INIT mask 0x2f and A630 SQE programming;
  its A6 fault checks and existing A7 tests pass. GPU contexts map A630 SQE
  at VA 0x400000 and can attach only nonsecure SID 0. Context tests preserve
  an existing secure SID 1 route and keep default A7 two-stream behavior.
  GMU contexts accept the legacy queue/log/debug allocation layout.
- The native SDM845 adapter binds already owned MMIO windows and UniXNU time
  callbacks. The common memory/VM/client/ring, SMMU, firmware and SDM845 kit
  compile as 30 ARM64/iOS objects using actual XNU recipes with -Werror.
  libSdm845GpuCore.a is a core archive, not a loadable accelerator.
- Sdm845GpuCommandDb parses a complete immutable bounded snapshot into GFX,
  MX and CX aliases, requiring qualified version and actual reservation size.
  902 malformed-layout/truncation checks pass. BluelineGpuPolicy constructs
  the stock GPU OPPs and only the common 200 MHz GMU level, with SDM845's
  fixed DDR/CNOC bandwidth table. Its 21 synthetic-rail checks pass. No actual
  command database has been read and no fallback rail data is provided.

## Historical integration tasks (2026-10-27)

Wire the SDM845 operations, qualified cmd-db votes and owned SMMU streams
to the native memory/VM components and driver lifecycle. Obtain actual clock
reference, command-db reservation/version, SMMU geometry and ownership inputs.
Load pinned SQE outside GMEM, execute A6 ME_INIT, authenticate PAS 13
ZAP and leave secure mode, then prove a device-written fence before publishing
AGXAccelerator. Qualify the 22H384 accelerator ABI, AdrenoMetal/Turnip and
GPU-only GopFramebuffer handoff. Stage any addon without changing the stable
persistent Data seed. Build/package only the resulting complete GPU candidate.

### Takeover plan (Claude, 2026-10-27)

Upstream check the same day: Adreno740 and UniXNU have nothing new for this
work. AdrenoMetal (pin 454ff2ce, head ce5e19e) and GopFramebuffer (pin cf3b4297,
head 33e941a, "scan the compositor's surface out directly") moved; take their
heads when integrating those components.

Board inputs settled from the stock DTB (.cache/firmware/stock-dtb/00.dtb):
- GPLL0 reference: the board XO, 19.2 MHz (`clock-frequency = <0x124f800>` on
  the arch timer and timer@17c90000). This is the `gpll_reference_hz` input.
- cmd-db: `/soc/qcom,cmd-db@861e0000` has `reg = <0xc3f000c 0x8>`, the AOP
  message-RAM pointer to the database (32-bit address, 32-bit size) that the
  stock msm-4.9 kernel reads. Linux v6.12 maps a reserved-memory region instead
  and validates only the magic (cmd-db.c:103-137), never the version word. So
  the driver can read the pointer at start, map that exact range, and pass the
  header's own version (logged) after the magic check, instead of a
  boot-argument version. (msm-4.9's own check is not verified here.)
- ZAP: `/soc/qcom,kgsl-hyp` pas-id 13, firmware a630_zap, memory-region
  gpu_region 0x8c415000/0x2000. ios-flame patch 0095 `zap_load` is a working
  A6 sequence (SCM PIL init image, mem setup, auth and reset); its A640 region
  0x9a915000 is not ours.
- GPU SMMU `/soc/arm,smmu-kgsl@5040000`, qcom,smmu-v2, `#iommu-cells = 1`.

22H384 IOGPUFamily metaclass sizes (OSMetaClass constructor `w3`, both
kernelcaches, ~/blueline/ios18-tools via /tmp/meta_size.py): 40 of 43 classes
unchanged. IOAcceleratorES stays 0x88 (136), matching AdrenoMetal's
OriginalAcceleratorABI.h static_assert. Changed: IOGPU 0x2e0→0x2e8 (the
unpublished OriginalGPUProviderABI.h probe asserts 736), IOGPUBlockFence
0x108→0x130, IOGPUMTLFence 0x98→0xa0; new IOGPUDeviceAssertion, IOGPUGroupMemory.

Sheng's integration (refs/UniXNU-sheng 08cde20): GopFramebuffer's legacy
display late probe (package/LegacyDisplayProbe.cpp:485) calls the board's
`sheng_gpu_power_probe`, which starts a kernel thread (GpuPowerProbe.cpp:2036)
running the staged bring-up (`sheng-gpu-power=9`), the zap shader through SCM
PAS 13 and CP_SET_SECURE_MODE 0 (:540-650), then `native_adreno_publish` and,
with `sheng-gpu-metal=1`, the Metal publication. Board archives are listed in
board.toml `archives` (build-adreno740-kits.py embeds GMU/SQE/ZAP with
`.incbin`). Blueline follows the same shape with kits/Sdm845Gpu; the merged
a630_zap.elf (pinned) replaces sheng's a740_zap.mbn.

Order of work:
1. GPU device-tree nodes in sdm845.dtsi (GPU, GMU, GPU SMMU, cmd-db pointer,
   ZAP region), from the stock DTB.
2. The A630 driver lifecycle (one IOService, SoC repository): map the windows,
   build the GMU and GPU SMMU contexts (SID 0 only), PowerCx, BootGmu,
   EnableHfi + legacy startup, HoldGpu, ConfigureHardware, SQE at 0x400000,
   ZAP through SCM PAS 13, A6 ME_INIT, and wait for the device-written fence.
   Every phase logs to the persistent /var log.
3. Publication (NativeAdrenoPublish) and the user client on ABI 2.
4. AdrenoMetal/Turnip for A630 (no A740 replay prefix), and GopFramebuffer in
   GPU-only scanout mode on the 22H384 ABI (DISPLAY-22H384.md).
5. Package the one-core, no-bars candidate on the existing Data seed.

The original plan noted that steps 2 and 3 could be phone-tested on their own: a kernel that brings the GPU up
to a completed fence and logs the result, with the screen left as it is (no CPU
rendering). Whether that test comes before the full Metal candidate is the
owner's decision. The owner subsequently chose the full GPU/Metal/display candidate, not an intermediate test.

Host checks are under build/gpu-tests and ~/blueline/gpu21-tests on the Mac.
At that checkpoint no kernel 21, new phone run, source commit or publication had occurred.

Core build (Mac):

```sh
source ~/blueline/env.sh
python3 ~/blueline/UniXNU-sdm845-common/kits/Sdm845Gpu/build-native.py \
  --tree ~/blueline/UniXNU/out/blueline/ipados-18.7.11-22H384/blueline/tree \
  --components ~/blueline/UniXNU
```

The build report records every command/source/object hash under the resolved
tree's build/sdm845-gpu-core/build-report.json. The host context script is
kits/Sdm845Gpu/tests/native-context.py in the SoC checkout. Its Intel-host
test peers do not qualify actual SMMU registers or stream ownership.

Board policy uses its own XNU object recipe, keeping board OPPs out of the
shared core archive:

```sh
python3 ~/blueline/UniXNU-blueline/package/build-gpu-policy.py \
  --tree ~/blueline/UniXNU/out/blueline/ipados-18.7.11-22H384/blueline/tree \
  --components ~/blueline/UniXNU --soc ~/blueline/UniXNU-sdm845-common
```
