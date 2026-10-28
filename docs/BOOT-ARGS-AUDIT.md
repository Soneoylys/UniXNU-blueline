# Startup and CPU audit (2026-10-27)

Kernel 17 combines the Pocket Max development startup fixes with the Pixel 3 iOS 16 port's CPU findings.
The candidate has built and passed the host checks in [PROGRESS.md](PROGRESS.md); its phone boot is pending.

References inspected:

- UniXNU-sheng `08cde20edc3254ed6f7df8d0f5384fb887786e8f`, its board arguments, defconfig and porting record.
- UniXNU's Pocket Max root-auth patch/checker at `bc5337b8d838267fb17a74b2314a436cf2199e53`, and SystemOS
  development patch at `2dc5b8dedf9a69c39d1a41f1aa9438dc1a8c5c21`, recovered from the existing UniXNU checkout.
  Pocket Max's recorded runs 8-13 establish the root-auth, ignition and signature-page failure sequence.
- [erikkunz/pixel3-ios](https://github.com/erikkunz/pixel3-ios) `5849d065053d76759e28202c12eb17eb2f589d28`:
  patches 0101, 0106, 0112 and 0117, README and docs/LOG.md.
- Blueline's unchanged 22H384 OS/SystemOS images and the stock Pixel 3 DTB used by the firmware packager.

| Issue or setting | Kernel 17 decision and evidence |
| --- | --- |
| `rootvp not authenticated after mounting` | Select the existing `zzy-debug-rootauth-bypass.patch`, `CONFIG_XNU_DEBUG_ROOTAUTH_BYPASS=y`, and exact `debug-rootauth-bypass=1`. Apple FSIOC_KERNEL_ROOTAUTH still executes; the development gate logs its failure and permits this owner's unverified root. 126 compiled cases cover default-off behavior, argument values and the owner path. |
| Sheng's owner verification/mount flags | Sheng uses verified block policies and `CONFIG_XNU_OWNER_*`. Blueline's owner chose unverified UFS; the verified owner path therefore stays disabled. The separate development mount supports that existing choice. |
| SystemOS/cache visibility | Select the Pocket Max read-only APFS union mount and `CONFIG_XNU_DEBUG_SYSTEMOS_UNION=y`. The new NativeUfs `native_debug_volume_device` accepts exactly one matching UUID below its attached SystemOS window; it returns a BSD device without asserting authentication. The UUID was measured as `3C5B8DDB-FEED-44F1-852D-F5F64FAB3EBB`, replacing the 21H461 default. OS contains launchd/dyld; SystemOS contains the cache. |
| dyld ignition/Preboot | `debug-systemos-union=2 ignition_level=1` is Pocket Max's recorded path to cache registration. Mode 2 leaves ignition enabled. Mode 1 sets dyld flag bit 3, so ignition_level cannot take effect. No Preboot volume is supplied. |
| Shared-cache signature registration | Explicit `CONFIG_XNU_NATIVE_CS_HASH_CACHE=y`, also inherited from common.config. The OS18 profile already rebases `native-4k-signature-hash-cache.patch`: original 16 KiB blocks are authenticated before read-only 4 KiB digests are published. Eight compiled actual-source gate cases pass; the final kernel has the verifier/version/counter symbols and its compiler record enables the flag. This verifies integration, not full hardware signature validation. |
| Apple trust-cache inputs | The actual payload contains the unchanged 22H384 Static/System bundle, SHA-256 `26ed8386ab1984442d2e65679bc4ed4544ba8a71dcfccdecaf39e5ceb6ef7ce0`, 2830+89 entries. All 62 measured SystemOS cache CodeDirectories use SHA-256 with page shift 14; every CDHash is present in that bundle. |
| Pocket Max `debug-cs-16k-unverified` | Its newer board describes a PIO SD startup shortcut. Blueline uses the existing UFS integrity-cache path above. This argument is not enabled. |
| Launch constraints / AMFI | Upstream's `zzyc-owner-skip-launch-constraints.patch` is selected by the profile, but `sheng-no-lc` is absent from both current Sheng and this candidate. No owner/AppOS daemon is being injected. AMFI and code signatures remain active; an actual constraint violation would need the specific process and reason. |
| `-apfs_shared_datavolume`, owner Data mount | Sheng supplies a Data volume/seed and persistence implementation. Blueline currently has neither. This argument cannot create them. Persistent Data and later daemon writable paths remain unresolved. |
| OS17 signed-text fixups | Sheng/Pocket Max's 21H461 cache offsets cannot be copied into 22H384. The kernel supports signed-text fixups, but any later userland workaround needs matching 22H384 file bytes and offsets. |
| Eight CPUs | Enable CPU affinities `0x000..0x700`, `cpus=8 sheng-smp=1 armcpu-self-ipi=1 armcpu-timer-intid=19`. `sheng-smp` is the existing generic ArmCpu gate. The SoC's existing secondary patch provides eight slots and Affinity1 reset matching. As pixel3-ios does, this native BCM2837 personality presents one SMP scheduling cluster; E/P tags would exceed its one-cluster limit. |
| CPU speed | `bluecpufreq=1` adapts pixel3-ios 0106 to ArmCpu in the SoC repository. Read the 40-row, 32-byte-stride OSM LUT, skip one-core turbo rows, request the highest regular index, and honor per-core DCVS. Nominal regular values reported by that Pixel 3 port are Silver 1.7664 GHz and Gold 2.6496 GHz; runtime LUT/readback logs establish this phone's requests. Disabled controllers receive no writes. No voltage or controller-enable register is changed. |
| CPU register evidence | Stock DTB `/soc/qcom,cpucc@0x17d41000` lists `osm_pwrcl_base=0x17d43000+0x1400`, `osm_perfcl_base=0x17d45800+0x1400`. Mainline DTB from pixel3-ios agrees. The SoC DTS exports these windows; ArmCpu translates the ADT arm-io bus addresses and requires those exact physical ranges. |
| SMP idle/stalls | SoC patches adapt pixel3-ios 0112 to omit the whole-cache set/way idle flush, retaining WFI, and 0117 to recover a repeated successful translation/write fault with VA-wide inner-shareable TLBI. `sdm845-fault-loop=1` is explicit, development-only, and never changes vm_fault's result. It is a bring-up workaround, not a general page-table teardown fix. |
| Sheng hardware arguments | SM8550 EPSS (`sheng-cpufreq`), SPI IPIs, PMGR, touch, GPU, Wi-Fi and watchdogd policy require their matching drivers/hardware. They are not copied into this SDM845 root-boot candidate. The board checker accepts ArmCpu's generic SMP gate while still rejecting the foreign clock policy. |

The archive also has a one-core diagnostic payload using the same kernel and fixes, with only `cpus=1` and
`sheng-smp=0` overriding the CPU startup settings. Both payloads were rebuilt after argument changes and checked
by extracting their actual FAT configuration, kernel, ADT and trust bundle. Hardware CPU count, requested/actual
clock speed, cache registration and userland startup remain separate phone observations.
