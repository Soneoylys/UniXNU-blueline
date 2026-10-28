# GopFramebuffer on 22H384: derived ABI (work in progress)

Source of the 21H461 facts: refs/UniXNU-sheng `scripts/build-gop-legacy-display.py`, `package/LegacyDisplayProbe.cpp`,
UniXNU `drivers/GopFramebuffer/NativeLegacyDisplay.cpp` and `scripts/audit-mobile-graphics-legacy.py`.
Kernelcache addresses (`out/ipsw/<build>/kernelcache.decompressed` on the build Mac); the prelinked kernel puts kext
code 0x1000000 lower. 21H461's IOMobileGraphicsFamily is unoptimized, 22H384's is optimized, so matches rest on field
offsets, constants and call structure, not code shape. Tools (Mac, amfi18-venv): /tmp/vt_align.py, slot_match.py,
show_slots.py, field_offsets.py (copies in the session scratchpad; to be moved into scripts/).

## Vtable (IOMobileFramebufferLegacy)

21H461 vtable via constructor 0xfffffff0067865d8 (358 slots); 22H384 0xfffffff006d4fd00 (368 slots).
Slots 0-167 (IOService) unchanged. Inserted in 22H384: 190, 222, 257, 265, 266, 267, 270, 274, 338, 342.

| Use | 21H461 | 22H384 | Evidence |
| --- | --- | --- | --- |
| abstract leaves | 211 213 245 246 306 307 337 338 345 | 212 214 247 248 314 315 347 348 355 | __cxa_pure_virtual |
| isMainDisplay | 184 | 184 | flag byte +0x1782 -> +0x189a |
| getDotPitch | 188 | 188 | property object +0x1640 -> +0x1758, same vcall 0x118 |
| supportedFrameInfo (abstract) | 213 | 214 | pure |
| getDisplayDimensions | 249 | 251 | width/height +5808/5812 -> +0x17c8/0x17cc (6088/6092) |
| startHardware | 288 | 296 | `return false`; caller check pending |
| kickSwapQueue | 290 | 298 | empty; caller check pending |
| completeCurrentSwap | 295 | 303 | `mov w1,#1; b completion` (0xfffffff005c1dfc0) |
| validateSwap | 296 | 304 | `return false`; caller check pending |
| getRefreshAttributes | 301 | 309 | 0x04444444 (1/60 s) constant |
| collectCompletionReports (abstract) | 337 | 347 | pure |

## Object fields (all confirmed in 22H384 code)

| Field | 21H461 | 22H384 | Evidence |
| --- | --- | --- | --- |
| instance size | 0x18a0 | 0x19c0 | OSMetaClass constructor at 0xfffffff005c27ae4 (`mov w3,#0x19c0`, super IOMobileFramebufferService 0xfffffff007bba038) |
| current swap | 5472 | 5736 | completion body |
| swap queue head / tail | 5480 / 5488 | 5744 / 5752 | completion body (`mov 5744` = &head; TAILQ tail follows the head) |
| client queues base (+ type*16) | 5512 | 5776 | from the vsync queue |
| vsync client queue (type 5) | 5592 | 5856 | vsync sender `ldr x19,[x19,#0x16e0]` |
| width / height | 5808 / 5812 | 6088 / 6092 | slot 251 getter |
| completed / presented counters (probe) | 5820 / 5824 | 6100 / 6104 | completion body stores |
| power-state pair | 5836 / 5840 | 6116 / 6120 | 0xfffffff005c23f18 loads/stores both, as 21H461's pair |
| request surfaces[4] | 2624 | 2632 | swap log: `ldr x0,[x19,#0xa48/#0xa50/#0xa58]` -> IOSurface ID getter (field 16), as 21H461's 0xa40/0xa48/0xa50 |
| request links next / prev | 2960 / 2968 | 3072 / 3080 | queue insert pairs and remove sequences (0xfffffff005c1fd6c, 0xfffffff005c1ded8, ...) |

Slot callers confirmed in 22H384: startHardware 296 (`tbz w0,#0` after the call), kickSwapQueue 298 (many sites),
validateSwap 304, report 347 (inside the completion body), completion 303, refresh 309 (setDisplayRefreshProperties),
getDisplayDimensions 251, isMainDisplay 184.

## Bindings (kernelcache addresses; the prelinked kernel puts kext code and data 0x1000000 lower)

| Symbol | 22H384 kernelcache | 21H461 (candidate = prelinked) |
| --- | --- | --- |
| `__ZN25IOMobileFramebufferLegacyC2EPK11OSMetaClass` | 0xfffffff005c1c034 | 0xfffffff0057865cc |
| `__ZN25IOMobileFramebufferLegacyD2Ev` | 0xfffffff005c1c058 | 0xfffffff005786618 |
| `__ZN25IOMobileFramebufferLegacy10gMetaClassE` | 0xfffffff007bbe580 | 0xfffffff006b35940 |
| `__ZTV25IOMobileFramebufferLegacy` | 0xfffffff006d4fcf0 | audit legacy_vptr - 16 |
| `_native_legacy_vsync_notify` | 0xfffffff005c23b18 | 0xfffffff005796720 |
| `_native_legacy_set_refresh_properties` | 0xfffffff005c26ef8 | 0xfffffff00579ebb8 |
| `_native_legacy_surface_descriptor` (x48) | 0xfffffff005a9ac60 | 0xfffffff004b9ead0 |
| `_native_legacy_surface_format` (w128) | 0xfffffff005a9ad24 | 0xfffffff004b9ebac |
| `_native_legacy_surface_width` (x88) | 0xfffffff005a9ad34 | 0xfffffff004b9ebbc |
| `_native_legacy_surface_height` (x96) | 0xfffffff005a9ad3c | 0xfffffff004b9ebc4 |
| `_native_legacy_surface_stride` (x104) | 0xfffffff005a9ad4c | 0xfffffff004b9ebd4 |
| `_native_legacy_surface_planes` (w152) | 0xfffffff005a9ad6c | 0xfffffff004b9ebf4 |
| `_native_legacy_surface_allocation` (x144) | 0xfffffff005a9ad74 | 0xfffffff004b9ebfc |

Other family facts in 22H384: IOMobileFramebufferService C2 0xfffffff005c17e94, D2 0xfffffff005c17eb8; six-word async
send 0xfffffff005c15efc; "IOMFBDisplayRefresh" string 0xfffffff005264301.

## Next

1. A 22H384 generator (based on refs/UniXNU-sheng `scripts/build-gop-legacy-display.py`): 368-slot header with the
   mapped owned slots and abstract leaves, size 0x19c0, the offsets above in the header helpers, bindings at the
   prelinked addresses (kernelcache - 0x1000000) after byte checks of each target.
2. Port `NativeLegacyDisplay.cpp` and `LegacyDisplayProbe.cpp` offsets (5592, 5836/5840, 2624, 5472/5480, 5808,
   5820/5824 → the values above) behind a per-OS table, not edits scattered through the source.
3. Wire XNU_NATIVE_LEGACY_DISPLAY into the 22H384 kernel, board args (sheng-vsync, display), then the iPhone identity.
