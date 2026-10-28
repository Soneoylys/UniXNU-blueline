// SPDX-License-Identifier: BSD-2-Clause-Patent
// Pixel 3 (SDM845) Adreno 630 bring-up and Metal publication, the blueline counterpart of UniXNU-sheng's
// package/GpuPowerProbe.cpp mode 9 path. `blueline-gpu=<n>`:
//   0  off (default)
//   1  bring the GPU up to a CP fence the device wrote, the zap shader and CP_SET_SECURE_MODE 0, then publish the native
//      Adreno service (native_adreno_publish)
//   2  as 1, then publish the accelerator for the original Metal discovery (NativeAdrenoMetal plugin, through the GPU addon)
// The sequence follows a6xx_gmu_resume / a6xx_hw_init for A630 (Linux v6.12) through kits/Sdm845Gpu of UniXNU-sdm845-common:
// CX power and GMU clock, the GMU's SMMU context, flat-ITCM GMU firmware, rail votes and the legacy (HFI v1) startup, GPU_SET,
// the GPU's SMMU context (A6 profile, SQE at 0x400000, one nonsecure stream), A630 hardware setup, SQE, ring, A6 ME_INIT, a
// CP_MEM_WRITE checked by the CPU, the zap shader through SCM PAS 13 into gpu_region 0x8c415000, CP_SET_SECURE_MODE 0, and
// BOOT_SLUMBER release. Every stage and result goes to the kernel log as "BluelineGpu:" lines (the persistent /var log
// keeps them) and to blueline_gpu_state[]; nothing is drawn. Any failure stops the sequence and retains every allocation.
// Board inputs, each from the stock boot.img DTB 0 of SP1A.210812.016.C2 (.cache/firmware/stock-dtb/00.dtb):
//   GCC 0x100000, GPUCC 0x5090000/0x9000, GMU 0x506a000/0x30000 (RSCC +0x23000), PDC 0xb200000 (register 0xb280000,
//   sequence 0xb480000), GPU 0x5000000/0x40000, GPU SMMU 0x5040000/0x10000 (qcom,smmu-v2), cmd-db pointer 0xc3f000c/8,
//   gpu_region 0x8c415000/0x2000 (qcom,kgsl-hyp pas-id 13), SIDs: GPU 0 (gfx3d_user), GMU 5 (gmu_kernel).
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IOPlatformExpert.h>
#include <libkern/c++/OSSharedPtr.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <string.h>
#include <stdarg.h>
#include "Sdm845Gpu.h"
#include "Sdm845GpuNative.h"
#include "Sdm845GpuCommandDb.h"
#include "BluelineGpuPolicy.h"
#include "AdrenoHfi.h"
#include "AdrenoHfiStartup.h"
#include "AdrenoPM4.h"
#include "ArmSmmu4KMmio.h"
#include "NativeAdrenoGmuContext.h"
#include "NativeAdrenoGpuContext.h"
#include "NativeAdrenoKernelTime.h"
#include "NativeAdrenoCpInit.h"
#include "NativeAdrenoRing.h"
#include "NativeAdrenoSubmit.h"
#include "NativeAdrenoSha256.h"
#include "NativeAdrenoTimer.h"
#include "NativeAdrenoABI.h"

extern "C" {
extern const uint8_t native_adreno_firmware_image[];         // a630_gmu.bin
extern const uint64_t native_adreno_firmware_image_size;
extern const uint8_t native_adreno_sqe_firmware_image[];     // a630_sqe.fw
extern const uint64_t native_adreno_sqe_firmware_image_size;
extern const uint8_t native_adreno_zap_image[];              // a630_zap.elf (the merged mdt + b00..b02)
extern const uint64_t native_adreno_zap_image_size;
IOReturn native_adreno_publish(NativeAdrenoVm *, NativeAdrenoSubmit *, NativeAdrenoRing *, const NativeAdrenoRingClock *,
                               const NativeAdrenoTimer *, const NativeAdrenoAbiDeviceInfo *, uint32_t, uint32_t)
    __attribute__((weak));
void native_adreno_accelerator_static_init(void) __asm__("__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp") __attribute__((weak));
IOService *native_adreno_accelerator_allocate(void) __attribute__((weak));
unsigned native_adreno_accelerator_instance_size(void) __attribute__((weak));
kern_return_t OSKextLoadKextWithIdentifier(const char *);
extern kmod_info_t g_kernel_kmod_info;
extern volatile uint32_t native_adreno_pool_mib;
volatile uint64_t native_adreno_fence_phys = 0;   // the page the CP writes its timestamps to (NativeAdrenoService.cpp defines it on sheng)
// [0] stage, [1] status of that stage, [2] Sdm845Gpu phase, [3] its window, [4] its offset, [5] its value
volatile uint32_t blueline_gpu_state[8];
}

namespace {
// SHA-256 pins of firmware/adreno630.json (stock vendor.img of SP1A.210812.016.C2).
constexpr uint8_t kGmuSha[32] = {0xb0, 0x7f, 0x90, 0x29, 0x9f, 0xf2, 0xae, 0x33, 0x94, 0x8f, 0xcf, 0xd6, 0xed, 0x67, 0xf5, 0x3f,
                                 0xbd, 0x28, 0x7b, 0x5b, 0xf8, 0x5f, 0xf2, 0xd4, 0x3d, 0xb1, 0x1d, 0x4d, 0x32, 0xc1, 0x62, 0x1f};
constexpr uint64_t kGmuBytes = 32768, kSqeBytes = 32136, kZapImageBytes = 14256;
constexpr uint32_t kChipId = 0x06030001;

enum {WinGcc, WinGpucc, WinGmu, WinRscc, WinPdc, WinPdcSeq, WinGpu, WinSmmu, WinCount};
constexpr uint64_t kPhys[WinCount] = {0x00100000, 0x05090000, 0x0506a000, 0x0508d000, 0x0b280000, 0x0b480000, 0x05000000,
                                      0x05040000};
constexpr uint64_t kLen[WinCount] = {0x80000, 0x9000, 0x30000, 0x1000, 0x10000, 0x1000, 0x40000, 0x10000};
static_assert(int(WinGcc) == int(Sdm845GpuGcc) && int(WinGpu) == int(Sdm845GpuRegisters) && int(WinSmmu) == int(Sdm845GpuWindows),
              "window order");
constexpr uint64_t kCmdDbPointer = 0x0c3f000c;
constexpr uint64_t kZapPhys = 0x8c415000, kZapBytes = 0x2000;
constexpr uint32_t kGpuPasId = 13;
// SMMU placement. The GPU takes bank 0 as arm-smmu-qcom gives it; the GMU the next bank. Both are refused when the
// firmware left their SMR slot valid. Override with blueline-gpu-{bank,slot,gmu-bank,gmu-slot}.
constexpr uint16_t kGpuSid = 0, kGmuSid = 5;

OSSharedPtr<IOMemoryMap> gMaps[WinCount];
volatile uint8_t *gVa[WinCount];
Sdm845GpuNative gNative;
Sdm845GpuIo gIo;
Sdm845GpuState gState;
NativeAdrenoGmuContext gGmuCtx;
NativeAdrenoGpuContext gGpuCtx;
OSSharedPtr<IOMemoryMap> gHfiMap, gGpuMaps[GpuBufferCount];
volatile uint8_t *gGpuCpu[GpuBufferCount];
AdrenoHfi gHfi;
AdrenoHfiStartupConfig gStartup;
uint8_t *gCmdDb;           // immutable snapshot, kept for the GPU lifetime (the ARC inputs alias it)
NativeAdrenoRing gRing;
NativeAdrenoCpInit gCpInit;
NativeAdrenoSubmit gSubmit;
NativeAdrenoTimer gTimer;
NativeAdrenoMemory gZapMetadata;
ArmSmmu4KMmio gSmmuMmio;
ArmSmmu4KIo gSmmuIo;

unsigned bootArg(const char *name, unsigned fallback) {
    unsigned value = fallback;
    PE_parse_boot_argn(name, &value, sizeof value);
    return value;
}
// On-screen status (blueline-gpu-status, default 1): one white console-font line per stage and the failing stage's
// registers, drawn into the lower part of the GOP framebuffer (below the loader's text) until the first GPU-copied frame
// replaces them, so a photo shows how far the GPU got. Text only, no bars or blocks. Each line is drawn and written to the
// UFS log (BluelineLog.c) before the caller goes on, so a step that stalls the bus on the single CPU still leaves it on the
// screen and on the medium.
extern "C" unsigned char iso_font[256 * 16];   // XNU osfmk/console/iso_font.c, 8x16
extern "C" volatile uint64_t native_gop_gpu_completions;
extern "C" volatile uint32_t blueline_display_state[12];
extern "C" volatile uint32_t boot_log_state __attribute__((weak_import)), boot_log_rounds __attribute__((weak_import));
extern "C" int blueline_log_checkpoint(const char *) __attribute__((weak_import));
// 12 + 44 * 24 = 1068 <= 1080 wide; 1180 + 20 * 48 = 2140 <= 2160 high
constexpr unsigned kLines = 20, kCols = 44, kScale = 3, kTop = 1180, kLeft = 12;
char gStatus[kLines][kCols + 1];
unsigned gStatusCount;
OSSharedPtr<IOMemoryMap> gFbMap;
volatile uint8_t *gFb;
PE_Video gVideo;
void drawLine(unsigned row, const char *text);
void drawAll() {
    if (!gFb || native_gop_gpu_completions) return;   // the GPU draws the screen now
    for (unsigned row = 0; row < gStatusCount; ++row) drawLine(row, gStatus[row]);
    __asm__ volatile("dsb sy" ::: "memory");
}
void screen(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void screen(const char *fmt, ...) {
    char line[kCols + 1];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    if (gStatusCount == kLines) {
        for (unsigned row = 1; row + 1 < kLines; ++row) memcpy(gStatus[row], gStatus[row + 1], sizeof gStatus[0]);   // keep line 0
        gStatusCount = kLines - 1;
    }
    if (!gStatusCount) gStatusCount = 1;   // line 0 is the display summary
    strlcpy(gStatus[gStatusCount++], line, sizeof gStatus[0]);
    IOLog("BluelineGpu: | %s\n", line);
    drawAll();
    if (&blueline_log_checkpoint) blueline_log_checkpoint(line);
}
void drawLine(unsigned row, const char *text) {
    const size_t stride = gVideo.v_rowBytes;
    const unsigned y0 = kTop + row * 16 * kScale;
    if (y0 + 16 * kScale > gVideo.v_height) return;
    for (unsigned gy = 0; gy < 16; ++gy)
        for (unsigned sy = 0; sy < kScale; ++sy) {
            volatile uint32_t *px = reinterpret_cast<volatile uint32_t *>(gFb + (y0 + gy * kScale + sy) * stride) + kLeft;
            bool ended = false;
            for (unsigned c = 0; c < kCols; ++c) {
                const unsigned char ch = ended || !text[c] ? ' ' : static_cast<unsigned char>(text[c]);
                if (!text[c]) ended = true;
                const unsigned char bits = iso_font[ch * 16 + gy];
                for (unsigned gx = 0; gx < 8; ++gx)
                    for (unsigned sx = 0; sx < kScale; ++sx) *px++ = (bits >> gx) & 1 ? 0xffffffffu : 0xff000000u;
            }
        }
}
// Line 0 every 2 s: frames the display took and refused, GPU-refused frames, transfers, and the UFS log's state and rounds
// (state 2 = writing). The other lines are drawn when they are added.
void statusThread(void *, wait_result_t) {
    for (unsigned tick = 0; tick < 600; ++tick) {   // 20 minutes at most
        if (native_gop_gpu_completions) return;      // the GPU draws the screen now
        snprintf(gStatus[0], sizeof gStatus[0], "disp %u/%u gpu-ref %u xfer %u log %u r%u", blueline_display_state[6],
                 blueline_display_state[7], blueline_display_state[8], blueline_display_state[9],
                 &boot_log_state ? boot_log_state : 0, &boot_log_rounds ? boot_log_rounds : 0);
        if (!gStatusCount) gStatusCount = 1;
        drawLine(0, gStatus[0]);
        __asm__ volatile("dsb sy" ::: "memory");
        IOSleep(2000);
    }
}
// The status lines, one per row (BluelineLog.c's text slot, through blueline_gpu_status_text).
size_t statusText(char *out, size_t cap) {
    size_t at = 0;
    for (unsigned row = 0; row < gStatusCount && at + kCols + 2 < cap; ++row) {
        const size_t n = strnlen(gStatus[row], kCols);
        memcpy(out + at, gStatus[row], n);
        at += n;
        out[at++] = '\n';
    }
    if (at < cap) out[at] = 0;
    return at;
}
void statusStart() {
    if (!bootArg("blueline-gpu-status", 1) || PE_current_console(&gVideo) || gVideo.v_depth != 32 || !gVideo.v_baseAddr ||
        gVideo.v_width < kLeft + kCols * 8 * kScale || gVideo.v_height <= kTop)
        return;
    IODeviceMemory *memory = IODeviceMemory::withRange(gVideo.v_baseAddr, gVideo.v_rowBytes * gVideo.v_height);
    if (!memory) return;
    gFbMap = OSSharedPtr<IOMemoryMap>(memory->map(kIOMapAnywhere | kIOMapWriteCombineCache), OSNoRetain);
    memory->release();
    if (!gFbMap) return;
    gFb = reinterpret_cast<volatile uint8_t *>(gFbMap->getVirtualAddress());
    gStatusCount = 1;
    thread_t thread = nullptr;
    if (kernel_thread_start(statusThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}
const char *const kStageNames[] = {"", "firmware", "windows", "cmd-db", "CX power", "GMU SMMU", "GMU boot", "rail/HFI",
                                   "HFI start", "GPU_SET", "GPU SMMU", "hw setup", "SQE", "ring", "ME_INIT", "CP write",
                                   "zap", "secure", "release", "publish", "Metal", "Metal ok"};
void status_line(uint32_t value) {screen("GPU %u %s", value, kStageNames[value]);}
// Clock and power words of the CX/GMU start, on any kit failure. GPUCC is read only once gcc_gpu_cfg_ahb_clk (its
// register clock) was enabled, i.e. from the CX phase on.
void dumpPower() {
    if (!gVa[WinGcc]) return;
    const volatile uint32_t *gcc = reinterpret_cast<const volatile uint32_t *>(gVa[WinGcc]);
    screen("gcc 71004 %x 44038 %x", gcc[0x71004 / 4], gcc[0x44038 / 4]);
    screen("gcc 7100c %x 71018 %x", gcc[0x7100c / 4], gcc[0x71018 / 4]);
    screen("gcc 52000 %x 52004 %x", gcc[0x52000 / 4], gcc[0x52004 / 4]);
    IOLog("BluelineGpu: gcc 71004 %08x 44038 %08x 7100c %08x 71018 %08x 52000 %08x 52004 %08x\n", gcc[0x71004 / 4],
          gcc[0x44038 / 4], gcc[0x7100c / 4], gcc[0x71018 / 4], gcc[0x52000 / 4], gcc[0x52004 / 4]);
    if (gState.phase < Sdm845GpuPhaseCx || !gVa[WinGpucc]) return;
    const volatile uint32_t *cc = reinterpret_cast<const volatile uint32_t *>(gVa[WinGpucc]);
    screen("gpucc 106c %x 1540 %x", cc[0x106c / 4], cc[0x1540 / 4]);
    screen("gpucc 1098 %x 109c %x", cc[0x1098 / 4], cc[0x109c / 4]);
    screen("gpucc 1120 %x 1124 %x", cc[0x1120 / 4], cc[0x1124 / 4]);
    IOLog("BluelineGpu: gpucc 106c %08x 1540 %08x 1098 %08x 109c %08x 1120 %08x 1124 %08x\n", cc[0x106c / 4], cc[0x1540 / 4],
          cc[0x1098 / 4], cc[0x109c / 4], cc[0x1120 / 4], cc[0x1124 / 4]);
}
void stage(uint32_t value, uint32_t status) {
    blueline_gpu_state[0] = value;
    blueline_gpu_state[1] = status;
    if (!status && value < sizeof kStageNames / sizeof kStageNames[0]) status_line(value);
}
bool fail(uint32_t at, uint32_t status, const char *what) {
    stage(at, status);
    IOLog("BluelineGpu: stage %u %s failed: 0x%x\n", at, what, status);
    screen("FAIL %u %s: %x", at, what, status);
    return false;
}
bool kitFail(uint32_t at, Sdm845GpuStatus status, const char *what) {
    blueline_gpu_state[2] = gState.phase;
    blueline_gpu_state[3] = gState.window;
    blueline_gpu_state[4] = uint32_t(gState.offset);
    blueline_gpu_state[5] = gState.value;
    IOLog("BluelineGpu: %s: status %d phase %u window %u offset 0x%lx value 0x%x polls %u\n", what, status, gState.phase,
          gState.window, (unsigned long)gState.offset, gState.value, gState.polls);
    screen("kit %d ph %u w%u +%lx =%x", status, gState.phase, gState.window, (unsigned long)gState.offset, gState.value);
    dumpPower();
    if (gState.phase == Sdm845GpuPhaseClocks && gVa[WinGcc]) {
        // GPLL0 (Fabia at GCC+0): mode, L, user ctl, opmode, fractional, and the APCS vote register 0x52000.
        const volatile uint32_t *gcc = reinterpret_cast<const volatile uint32_t *>(gVa[WinGcc]);
        IOLog("BluelineGpu: GPLL0 mode %08x L %08x user %08x opmode %08x frac %08x vote %08x\n", gcc[0], gcc[1], gcc[3],
              gcc[0x2c / 4], gcc[0x38 / 4], gcc[0x52000 / 4]);
    }
    return fail(at, uint32_t(status), what);
}

volatile uint8_t *mapPhys(uint64_t phys, uint64_t bytes, IOOptionBits cache, OSSharedPtr<IOMemoryMap> &keep) {
    IODeviceMemory *memory = IODeviceMemory::withRange(phys, bytes);
    if (!memory) return nullptr;
    keep = OSSharedPtr<IOMemoryMap>(memory->map(kIOMapAnywhere | cache), OSNoRetain);
    memory->release();
    return keep ? reinterpret_cast<volatile uint8_t *>(keep->getVirtualAddress()) : nullptr;
}

// GPU register access in dword offsets through the kit's bound GPU window.
int gpuWrite(void *, uint32_t reg, uint32_t value) {return gIo.write32(gIo.cookie, Sdm845GpuRegisters, size_t(reg) * 4, value);}
int gpuRead(void *, uint32_t reg, uint32_t *value) {return gIo.read32(gIo.cookie, Sdm845GpuRegisters, size_t(reg) * 4, value);}
int barrier(void *) {__asm__ volatile("dsb sy" ::: "memory"); return 0;}
// Ring, memptrs and SQE are Normal non-cacheable for the GPU and write-combined for the CPU: ordering only.
int ringPublish(void *, const volatile uint32_t *, size_t) {return barrier(nullptr);}
int ringAcquire(void *, volatile uint32_t *, size_t) {return barrier(nullptr);}
uint64_t ringNow(void *) {uint64_t us = 0; NativeAdrenoKernelTime::now_us(nullptr, &us); return us;}
int ringIdle(void *) {IODelay(20); return 0;}
int timerRead(void *, uint32_t reg, uint32_t *value) {return gpuRead(nullptr, reg, value);}
const NativeAdrenoTimeIo gTimerIo{nullptr, timerRead};
// HOST2GMU_INTR_SET (a6xx_gmu.xml 0x5194, as a6xx_hfi_queue_write rings the GMU).
int hfiNotify(void *) {return gIo.write32(gIo.cookie, Sdm845GpuGmu, 0x5194 * 4, 1);}
int hfiNow(void *, uint64_t *us) {return NativeAdrenoKernelTime::now_us(nullptr, us);}
int hfiWait(void *, unsigned us) {return NativeAdrenoKernelTime::wait_us(nullptr, us);}
int hfiHealth(void *) {return gState.poisoned ? 1 : 0;}

// Facts for Turnip: chip and GMEM from a6xx_catalog.c (a630), UBWC 2.0 / swizzle levels 2+3 / bank bit 15 / four-channel
// macrotile from the sdm845 UBWC data, GMEM window and trap base as Sdm845GpuConfigureHardware programs them. The client
// window starts above the kernel buffers (SQE 0x400000 is the highest) and ends at 4 GiB.
const NativeAdrenoAbiDeviceInfo gDeviceInfo = {kChipId, 1u << 20, 0x100000ull, 0x1fffffffff000ull, 0x20000000ull,
                                               0xE0000000ull, 15, 6, 0, 2, NativeAdrenoDeviceClientIova, 0};

bool smrFree(volatile uint8_t *smmu, unsigned slot) {
    return !(reinterpret_cast<volatile uint32_t *>(smmu + 0x800)[slot] & (1u << 31));
}
// A context bank is taken when a valid stream-match entry translates to it (S2CR type 0, CBNDX = bank).
bool bankFree(volatile uint8_t *smmu, unsigned bank) {
    const volatile uint32_t *r = reinterpret_cast<const volatile uint32_t *>(smmu);
    const unsigned smrs = r[0x20 / 4] & 0xff;
    for (unsigned i = 0; i < smrs && i < 128; ++i)
        if ((r[0x800 / 4 + i] & (1u << 31)) && !((r[0xc00 / 4 + i] >> 16) & 3) && (r[0xc00 / 4 + i] & 0xff) == bank) return false;
    return true;
}
// Keep the preferred slot/bank when free, else take the next free one (ID0.NUMSMRG, ID1.NUMCB); `avoid` is a slot or
// bank already chosen for the other context. Reports the choice.
bool pickSmmu(const char *who, unsigned &bank, unsigned &slot, unsigned avoidBank, unsigned avoidSlot) {
    const volatile uint32_t *r = reinterpret_cast<const volatile uint32_t *>(gVa[WinSmmu]);
    const unsigned smrs = r[0x20 / 4] & 0xff, banks = r[0x24 / 4] & 0xff;
    const unsigned wantBank = bank, wantSlot = slot;
    for (unsigned n = 0; n < smrs && (!smrFree(gVa[WinSmmu], slot) || slot == avoidSlot); ++n) slot = (wantSlot + n + 1) % smrs;
    for (unsigned n = 0; n < banks && (!bankFree(gVa[WinSmmu], bank) || bank == avoidBank); ++n) bank = (wantBank + n + 1) % banks;
    const bool ok = smrFree(gVa[WinSmmu], slot) && slot != avoidSlot && bankFree(gVa[WinSmmu], bank) && bank != avoidBank;
    IOLog("BluelineGpu: %s SMMU bank %u slot %u (wanted %u/%u; %u banks, %u SMRs)\n", who, bank, slot, wantBank, wantSlot, banks, smrs);
    screen("%s smmu bank %u slot %u of %u/%u", who, bank, slot, banks, smrs);
    return ok;
}
unsigned gGmuBank = ~0u, gGmuSlot = ~0u;
// Every SMMU register access is one 32-bit volatile load or store. The SDM845 hypervisor traps the SMMU and emulates
// only single loads/stores with a valid syndrome; run 34 froze here when the compiler merged ID0/ID1 into one ldp
// through a non-volatile pointer.
void smmuSurvey(volatile uint8_t *smmu) {
    const volatile uint32_t *r = reinterpret_cast<const volatile uint32_t *>(smmu);
    const uint32_t scr0 = r[0];
    screen("smmu scr0 %x", scr0);
    const uint32_t id0 = r[0x20 / 4];
    const uint32_t id1 = r[0x24 / 4];
    const uint32_t id2 = r[0x28 / 4];
    const uint32_t gfsr = r[0x48 / 4];
    IOLog("BluelineGpu: SMMU scr0=%08x id0=%08x id1=%08x id2=%08x gfsr=%08x\n", scr0, id0, id1, id2, gfsr);
    screen("smmu id0 %x id1 %x id2 %x", id0, id1, id2);
    const unsigned smrs = id0 & 0xff;
    for (unsigned i = 0; i < smrs && i < 32; ++i) {
        const uint32_t smr = r[0x800 / 4 + i];
        const uint32_t s2cr = r[0xc00 / 4 + i];
        if (smr & (1u << 31)) IOLog("BluelineGpu: SMR%u=%08x S2CR%u=%08x (in use)\n", i, smr, i, s2cr);
    }
}

bool loadCommandDb(BluelineGpuArcInputs &arcs) {
    OSSharedPtr<IOMemoryMap> pointerMap, dbMap;
    volatile uint8_t *pointer = mapPhys(kCmdDbPointer & ~0xfffull, 0x1000, kIOMapInhibitCache, pointerMap);
    if (!pointer) return fail(3, 1, "cmd-db pointer map");
    const volatile uint32_t *words = reinterpret_cast<const volatile uint32_t *>(pointer + (kCmdDbPointer & 0xfff));
    const uint64_t base = words[0], bytes = words[1];
    IOLog("BluelineGpu: cmd-db at 0x%llx, 0x%llx bytes\n", base, bytes);
    if (!base || (base & 0xfff) || bytes < 144 || bytes > 0x100000) return fail(3, 2, "cmd-db pointer");
    volatile uint8_t *db = mapPhys(base, (bytes + 0xfff) & ~0xfffull, kIOMapInhibitCache, dbMap);
    if (!db) return fail(3, 3, "cmd-db map");
    gCmdDb = static_cast<uint8_t *>(IOMallocData(size_t(bytes)));
    if (!gCmdDb) return fail(3, 4, "cmd-db copy");
    for (uint64_t i = 0; i < bytes; i += 4) {
        const uint32_t w = *reinterpret_cast<const volatile uint32_t *>(db + i);
        memcpy(gCmdDb + i, &w, bytes - i < 4 ? size_t(bytes - i) : 4);
    }
    // Linux cmd-db.c validates only the magic; the parser checks it and the layout, and the version is the one recorded.
    uint32_t version = 0;
    memcpy(&version, gCmdDb, 4);
    IOLog("BluelineGpu: cmd-db version 0x%x\n", version);
    Sdm845GpuArcInputs in{};
    const Sdm845GpuDbStatus parsed = Sdm845GpuCommandDbArcInputs(gCmdDb, size_t(bytes), version ? version : 1, &in);
    if (parsed != Sdm845GpuDbSuccess) return fail(3, 0x10 + parsed, "cmd-db parse");
    arcs = {kChipId, in.gfx, in.mx, in.cx, in.gfx_bytes, in.mx_bytes, in.cx_bytes};
    return true;
}

bool firmwareValid() {
    uint8_t digest[32];
    if (native_adreno_firmware_image_size != kGmuBytes || native_adreno_sqe_firmware_image_size != kSqeBytes ||
        native_adreno_zap_image_size != kZapImageBytes)
        return fail(1, 1, "firmware sizes");
    NativeAdrenoSha256::hash(native_adreno_firmware_image, size_t(kGmuBytes), digest);
    if (memcmp(digest, kGmuSha, 32)) return fail(1, 2, "GMU firmware pin");
    return true;
}

// The GMU's own SMMU context with the legacy A630 payload layout (flat ITCM: one unused icache page, HFI v1).
bool gmuContext() {
    unsigned bank = bootArg("blueline-gpu-gmu-bank", 1), slot = bootArg("blueline-gpu-gmu-slot", 1);
    if (!pickSmmu("GMU", bank, slot, 0, ~0u)) return fail(4, slot, "GMU SMMU slot/bank");   // bank 0 stays for the GPU
    gGmuBank = bank;
    gGmuSlot = slot;
    static NativeAdrenoGmuContextReport report;
    gSmmuMmio = {reinterpret_cast<volatile unsigned char *>(gVa[WinSmmu]), size_t(kLen[WinSmmu])};
    if (ArmSmmu4KBindMmio(&gSmmuMmio, &gSmmuIo) != ArmSmmu4KSuccess) return fail(4, 1, "SMMU MMIO bind");
    const NativeAdrenoGmuSizes sizes = NativeAdrenoGmuA630Sizes(NativeAdrenoGmuContext::TablePages * 4096);
    const IOReturn status = gGmuCtx.bringUp(&gSmmuIo, bank, 1, slot, kGmuSid, nullptr, &report, &sizes);
    IOLog("BluelineGpu: GMU context bank %u slot %u: step %u status 0x%x program %u/%u route %u/%u ttbr0 0x%llx\n", bank, slot,
          report.step, status, report.program_status, report.program_phase, report.route_status, report.routes_verified,
          report.ttbr0);
    return status ? fail(4, status, "GMU context") : true;
}

bool hfiQueues() {
    NativeAdrenoMemory &hfi = gGmuCtx.allocator().hfi();
    if (hfi.spanCount() != 1) return fail(5, 1, "HFI span");
    volatile uint8_t *cpu = mapPhys(hfi.spans()[0].physical, 16384, kIOMapWriteCombineCache, gHfiMap);
    if (!cpu) return fail(5, 2, "HFI map");
    const AdrenoHfiStatus s = AdrenoHfiInitializeStoppedVersion(&gHfi, reinterpret_cast<volatile uint32_t *>(cpu), 16384,
        gGmuCtx.allocator().layout().hfi_iova, nullptr, hfiNotify, AdrenoHfiWireLegacy);
    return s ? fail(5, 0x10 + s, "HFI queues") : true;
}

bool gpuContext() {
    unsigned bank = bootArg("blueline-gpu-bank", 0), slot = bootArg("blueline-gpu-slot", 0);
    if (!pickSmmu("GPU", bank, slot, gGmuBank, gGmuSlot)) return fail(10, slot, "GPU SMMU slot/bank");
    static NativeAdrenoGmuContextReport report;
    {unsigned pool = bootArg("blueline-gpu-pool", 768); native_adreno_pool_mib = pool;}
    const unsigned slots[2] = {slot, 0};
    const uint16_t sids[2] = {kGpuSid, 0};
    const IOReturn status = gGpuCtx.bringUp(&gSmmuIo, bank, 2, slots, sids, nullptr, &report, 1, NativeAdrenoRingA6xx);
    IOLog("BluelineGpu: GPU context bank %u slot %u: step %u status 0x%x program %u route %u/%u spans %u\n", bank, slot,
          report.step, status, report.program_status, report.route_status, report.routes_verified, report.spans);
    if (status) return fail(10, status, "GPU context");
    for (unsigned i = 0; i < GpuBufferCount; ++i) {
        const NativeAdrenoMemory &b = gGpuCtx.buffer(i);
        if (b.spanCount() != 1) return fail(10, 0x20 + i, "GPU buffer span");
        gGpuCpu[i] = mapPhys(b.spans()[0].physical, b.bytes(), kIOMapWriteCombineCache, gGpuMaps[i]);
        if (!gGpuCpu[i]) return fail(10, 0x30 + i, "GPU buffer map");
    }
    return true;
}

// SMCCC SiP call with QCOM_SCM_INTERRUPTED resume and EBUSY retry (sheng scmCall, AdspProbe scm).
struct ScmResult {int64_t x0; uint64_t x1; uint32_t resumes;};
ScmResult scmCall(uint64_t fn, uint64_t arginfo, uint64_t p0, uint64_t p1, uint64_t p2) {
    uint64_t first = fn, a6 = 0;
    for (unsigned resume = 0, busy = 0; resume < 10000;) {
        register uint64_t x0 asm("x0") = first, x1 asm("x1") = arginfo, x2 asm("x2") = p0, x3 asm("x3") = p1;
        register uint64_t x4 asm("x4") = p2, x5 asm("x5") = 0, x6 asm("x6") = a6, x7 asm("x7") = 0;
        asm volatile("smc #0" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3), "+r"(x4), "+r"(x5), "+r"(x6), "+r"(x7)
                     :: "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17", "memory");
        if (x0 == 1) {first = 1; a6 = x6; ++resume; continue;}
        if (int64_t(x0) == -12 && busy++ < 20) {first = fn; a6 = 0; IOSleep(30); continue;}
        return {int64_t(x0), x1, resume};
    }
    return {-1000, 0, 10000};
}


// a6xx_zap_shader_init: qcom_mdt_load (PAS 13 init_image, mem_setup, segment copy into gpu_region, auth_and_reset), then
// CP_SET_SECURE_MODE 0 through the ring. Same order and checks as sheng's zapAndSecure.
bool zapAndSecure() {
    const uint8_t *fw = native_adreno_zap_image;
    const uint64_t size = native_adreno_zap_image_size;
    auto rd32 = [&](uint64_t at) {uint32_t v; memcpy(&v, fw + at, 4); return v;};
    auto rd16 = [&](uint64_t at) {uint16_t v; memcpy(&v, fw + at, 2); return v;};
    if (size < 52 || memcmp(fw, "\x7f" "ELF", 4) || fw[4] != 1) return fail(16, 1, "zap ELF header");
    const uint32_t phoff = rd32(28);
    const unsigned phent = rd16(42), phnum = rd16(44);
    if (phent != 32 || phnum < 2 || phnum > 16 || phoff + uint64_t(phnum) * 32 > size) return fail(16, 2, "zap headers");
    auto ph = [&](unsigned i, unsigned field) {return rd32(phoff + i * 32 + field * 4);};   // type off va pa filesz memsz flags
    unsigned hash = 0;
    for (unsigned i = 1; i < phnum && !hash; ++i) if (((ph(i, 6) >> 24) & 7) == 2) hash = i;
    if (ph(0, 0) == 1 || !hash) return fail(16, 3, "zap hash segment");
    uint64_t lo = ~0ull, hi = 0;
    bool relocate = false;
    for (unsigned i = 0; i < phnum; ++i) {
        if (ph(i, 0) != 1 || ((ph(i, 6) >> 24) & 7) == 2 || !ph(i, 5)) continue;
        if (ph(i, 6) & (1u << 27)) relocate = true;
        lo = lo < ph(i, 3) ? lo : ph(i, 3);
        const uint64_t end = (uint64_t(ph(i, 3)) + ph(i, 5) + 0xfff) & ~0xfffull;
        hi = hi > end ? hi : end;
    }
    if (lo >= hi || hi - lo > kZapBytes) return fail(16, 4, "zap segments");
    const uint64_t ehdr = ph(0, 4), hashBytes = ph(hash, 4), hashOff = ph(hash, 1);
    if (ehdr > size || hashOff + hashBytes > size || ehdr + hashBytes > 0x4000) return fail(16, 5, "zap metadata");
    const size_t metaBytes = size_t((ehdr + hashBytes + 0xfff) & ~0xfffull);
    if (gZapMetadata.allocate(metaBytes, 40, true, metaBytes) || gZapMetadata.spanCount() != 1)
        return fail(16, 6, "zap metadata buffer");
    uint8_t *meta = static_cast<uint8_t *>(gZapMetadata.mapping());
    memcpy(meta, fw, size_t(ehdr));
    memcpy(meta + ehdr, fw + hashOff, size_t(hashBytes));
    if (gZapMetadata.publishForDevice(0, metaBytes, true)) return fail(16, 7, "zap metadata publish");
    auto pil = [](uint32_t cmd) {return uint64_t(0x42000000u | (0x02u << 8) | cmd);};
    ScmResult r = scmCall(pil(0x01), 2 | (2u << 6), kGpuPasId, gZapMetadata.spans()[0].physical, 0);
    IOLog("BluelineGpu: zap init_image %lld/%llx (%u resumes)\n", r.x0, r.x1, r.resumes);
    if (r.x0 || r.x1) return fail(16, 8, "zap init_image");
    if (relocate) {
        r = scmCall(pil(0x02), 3, kGpuPasId, kZapPhys, hi - lo);
        IOLog("BluelineGpu: zap mem_setup %lld/%llx\n", r.x0, r.x1);
        if (r.x0 || r.x1) return fail(16, 9, "zap mem_setup");
    }
    {
        OSSharedPtr<IOMemoryMap> keep;
        volatile uint8_t *region = mapPhys(kZapPhys, kZapBytes, kIOMapWriteCombineCache, keep);
        if (!region) return fail(16, 10, "zap region map");
        const uint64_t base = relocate ? lo : kZapPhys;
        for (unsigned i = 0; i < phnum; ++i) {
            if (ph(i, 0) != 1 || ((ph(i, 6) >> 24) & 7) == 2 || !ph(i, 5)) continue;
            const uint64_t at = uint64_t(ph(i, 3)) - base, file = ph(i, 4), mem = ph(i, 5), off = ph(i, 1);
            if (ph(i, 3) < base || at + mem > kZapBytes || file > mem || off + file > size) return fail(16, 11, "zap segment");
            for (uint64_t b = 0; b < file; ++b) region[at + b] = fw[off + b];
            for (uint64_t b = file; b < mem; ++b) region[at + b] = 0;
        }
        barrier(nullptr);
    }
    r = scmCall(pil(0x05), 1, kGpuPasId, 0, 0);
    IOLog("BluelineGpu: zap auth_and_reset %lld/%llx (%u resumes)\n", r.x0, r.x1, r.resumes);
    if (r.x0 || r.x1) return fail(16, 12, "zap auth_and_reset");
    uint32_t words[2] = {}, fence = 0, seen = 0;
    if (AdrenoPM4Type7(0x66, 1, &words[0]) != AdrenoPM4Success || gRing.submit(words, 2, &fence))
        return fail(17, 1, "CP_SET_SECURE_MODE submit");
    const NativeAdrenoRingClock clock{nullptr, ringNow, ringIdle};
    const int waited = int(gRing.wait(fence, &clock, 200000, &seen));
    return waited ? fail(17, 0x10 + waited, "CP_SET_SECURE_MODE fence") : true;
}

bool cpStart() {
    // SQE: adreno_fw_create_bo copies the firmware without its first dword.
    const uint64_t sqe = native_adreno_sqe_firmware_image_size;
    if (sqe - 4 > gGpuCtx.buffer(0).bytes()) return fail(12, 1, "SQE size");
    for (uint64_t i = 4; i < sqe; ++i) gGpuCpu[0][i - 4] = native_adreno_sqe_firmware_image[i];
    barrier(nullptr);
    for (uint64_t i = 4; i < sqe; ++i)
        if (gGpuCpu[0][i - 4] != native_adreno_sqe_firmware_image[i]) return fail(12, 2, "SQE readback");
    const NativeAdrenoRingIo rio{nullptr, gpuWrite, gpuRead, barrier, nullptr, nullptr};
    const NativeAdrenoRingCache cache{nullptr, ringPublish, ringAcquire};
    native_adreno_fence_phys = gGpuCtx.buffer(2).spans()[0].physical;
    IOReturn status = gRing.attach(&rio, &cache, const_cast<uint8_t *>(gGpuCpu[1]), gGpuCtx.buffer(1).bytes(),
                                   gGpuCtx.iova(1), const_cast<uint8_t *>(gGpuCpu[2]), gGpuCtx.buffer(2).bytes(),
                                   gGpuCtx.iova(2), NativeAdrenoRingA6xx);
    if (status) return fail(13, status, "ring attach");
    NativeAdrenoCpInitConfig config{gGpuCtx.iova(0), 0, 0, NativeAdrenoRingA6xx};
    uint32_t fence = 0, seen = 0;
    status = gCpInit.run(&rio, &gRing, &config, &fence);
    if (status) return fail(14, status, "ME_INIT");
    const NativeAdrenoRingClock clock{nullptr, ringNow, ringIdle};
    int waited = int(gRing.wait(fence, &clock, 200000, &seen));
    IOLog("BluelineGpu: ME_INIT fence %u: result %d seen %u\n", fence, waited, seen);
    if (waited) return fail(14, 0x10 + waited, "ME_INIT fence");
    // First command the CPU can check: CP_MEM_WRITE (0x3d) of a pattern into the scratch page.
    volatile uint32_t *scratch = reinterpret_cast<volatile uint32_t *>(gGpuCpu[4]);
    scratch[0] = 0;
    barrier(nullptr);
    uint32_t words[4] = {};
    if (AdrenoPM4Type7(0x3d, 3, &words[0]) != AdrenoPM4Success) return fail(15, 1, "PM4");
    words[1] = uint32_t(gGpuCtx.iova(4));
    words[2] = uint32_t(gGpuCtx.iova(4) >> 32);
    words[3] = 0x5e9a630;
    if ((status = gRing.submit(words, 4, &fence))) return fail(15, status, "CP_MEM_WRITE submit");
    waited = int(gRing.wait(fence, &clock, 200000, &seen));
    const uint32_t value = scratch[0];
    IOLog("BluelineGpu: CP_MEM_WRITE fence %d, scratch 0x%x\n", waited, value);
    if (waited || value != 0x5e9a630) return fail(15, waited ? 0x10 + waited : 2, "CP_MEM_WRITE");
    return true;
}

// Kit MMIO through the board: the first access to each window and the first access after each kit phase change get a status
// line (drawn and logged before the access), so a register that stalls the bus is narrowed to one window and phase.
Sdm845GpuIo gRawIo;
bool gTouched[WinCount];
uint32_t gLastPhase = ~0u;
const char *const kWinNames[WinCount] = {"gcc", "gpucc", "gmu", "rscc", "pdc", "pdc-seq", "gpu", "smmu"};
void mark(unsigned w, size_t at, char op) {
    const bool first = w < WinCount && !gTouched[w], phase = gState.phase != gLastPhase;
    if (!first && !phase) return;
    if (w < WinCount) gTouched[w] = true;
    gLastPhase = gState.phase;
    screen("ph %u %c %s+%lx%s", gState.phase, op, w < WinCount ? kWinNames[w] : "?", (unsigned long)at, first ? " first" : "");
}
int ioRead(void *cookie, unsigned w, size_t at, uint32_t *value) {mark(w, at, 'r'); return gRawIo.read32(cookie, w, at, value);}
int ioWrite(void *cookie, unsigned w, size_t at, uint32_t value) {mark(w, at, 'w'); return gRawIo.write32(cookie, w, at, value);}

bool bringUp() {
    stage(1, 0);
    if (!firmwareValid()) return false;
    for (unsigned w = 0; w < WinCount; ++w) {
        gVa[w] = mapPhys(kPhys[w], kLen[w], kIOMapInhibitCache, gMaps[w]);
        if (!gVa[w]) return fail(2, w, "window map");
    }
    Sdm845GpuNativeWindow windows[Sdm845GpuWindows];
    for (unsigned w = 0; w < Sdm845GpuWindows; ++w) windows[w] = {gVa[w], size_t(kLen[w])};
    // The board XO (the arch timer's clock-frequency 0x124f800 in the stock DTB) feeds GPLL0.
    Sdm845GpuStatus s = gNative.bind(windows, 19200000, &gIo);
    if (s) return kitFail(2, s, "MMIO bind");
    gRawIo = gIo;
    gIo.read32 = ioRead;
    gIo.write32 = ioWrite;
    BluelineGpuArcInputs arcs{};
    if (!loadCommandDb(arcs)) return false;
    if (BluelineGpuBuildStartup(&arcs, &gStartup) != AdrenoHfiStartupSuccess) return fail(3, 0x20, "startup table");
    stage(4, 0);
    if ((s = Sdm845GpuPowerCx(&gState, &gIo))) return kitFail(4, s, "CX power");
    IOLog("BluelineGpu: CX on, GPLL0 even %u Hz\n", gState.gpll_even_hz);
    screen("CX on; reading the GPU SMMU");
    smmuSurvey(gVa[WinSmmu]);
    if (!gmuContext() || !hfiQueues()) return false;
    stage(6, 0);
    const NativeAdrenoGmuMemoryLayout &layout = gGmuCtx.allocator().layout();
    const Sdm845GpuGmuConfig gmu{native_adreno_firmware_image, size_t(kGmuBytes), kChipId, uint32_t(layout.hfi_iova),
                                 uint32_t(layout.log_iova), uint32_t(gGmuCtx.allocator().log().bytes()), 1};
    if ((s = Sdm845GpuBootGmu(&gState, &gIo, &gmu))) return kitFail(6, s, "GMU boot");
    // a6xx_gmu_gfx_rail_on: the highest GPU level's GX/MX ARC indices.
    const uint32_t vote = gStartup.gpu[gStartup.gpu_count - 1].vote;
    stage(7, 0);
    if ((s = Sdm845GpuEnableHfi(&gState, &gIo, uint8_t(vote), uint8_t(vote >> 8)))) return kitFail(7, s, "rail/HFI");
    stage(8, 0);
    static AdrenoHfiStartup startup;
    static AdrenoHfiStartupResult result;
    const AdrenoHfiLegacyBoot boot{uint32_t(layout.debug_iova), uint32_t(gGmuCtx.allocator().debug().bytes()), 1};
    const AdrenoHfiStartupServices services{nullptr, hfiNow, hfiWait, hfiHealth, 1000, 100, 100000, 1000000};
    const AdrenoHfiStartupStatus hs = AdrenoHfiStartupRunLegacy(&startup, &gHfi, &gStartup, &boot, &services, &result);
    IOLog("BluelineGpu: HFI legacy startup %d: stage %u transport %d fw error 0x%x acks 0x%x\n", hs, result.stage,
          result.transport_status, result.firmware_error, result.acknowledged_stages);
    if (hs) return fail(8, hs, "HFI startup");
    stage(9, 0);
    // Stock initial power level 6 of 8 (qcom,initial-pwrlevel; level 0 of the stock list is 710 MHz) is 257 MHz: index 1 here.
    if ((s = Sdm845GpuHoldGpu(&gState, &gIo, 1, bootArg("blueline-gpu-perf", 1), gStartup.gpu_count)))
        return kitFail(9, s, "GPU_SET");
    if (!gpuContext()) return false;
    stage(11, 0);
    const Sdm845GpuHardwareConfig hw{kChipId, 1u << 20, 15, 32, 2};
    if ((s = Sdm845GpuConfigureHardware(&gState, &gIo, &hw))) return kitFail(11, s, "hardware setup");
    if (!cpStart()) return false;
    if (bootArg("blueline-gpu-zap", 1) && !zapAndSecure()) return false;
    stage(18, 0);
    if ((s = Sdm845GpuFinishBoot(&gState, &gIo, 1))) return kitFail(18, s, "boot release");
    IOLog("BluelineGpu: GPU ready\n");
    screen("GPU ready: CP fence + zap ok");
    return true;
}

bool publish() {
    stage(19, 0);
    NativeAdrenoVm *vm = gGpuCtx.vm();
    if (!native_adreno_publish || !vm) return fail(19, 1, "publish link");
    if (IOReturn status = gSubmit.attach(vm, &gRing)) return fail(19, status, "submit attach");
    const IOReturn timer = gTimer.attach(&gTimerIo, NativeAdrenoTimeCp);
    const NativeAdrenoRingClock clock{nullptr, ringNow, ringIdle};
    const IOReturn published = native_adreno_publish(vm, &gSubmit, &gRing, &clock, timer ? nullptr : &gTimer, &gDeviceInfo, 32, 40);
    IOLog("BluelineGpu: native Adreno service published: 0x%x\n", published);
    return published ? fail(19, published, "publish") : true;
}

// As sheng's publishMetal: register the accelerator class under the original IOAcceleratorES (0x88 bytes in 22H384 as in
// 21H461) and give the Metal discovery the plugin keys.
bool publishMetal() {
    stage(20, 0);
    if (!native_adreno_accelerator_static_init || !native_adreno_accelerator_allocate || !native_adreno_accelerator_instance_size)
        return fail(20, 1, "accelerator link");
    if (kern_return_t k = OSKextLoadKextWithIdentifier("com.apple.iokit.IOGPUFamily")) return fail(20, k, "IOGPUFamily load");
    const OSSymbol *name = OSSymbol::withCString("IOAcceleratorES");
    const OSMetaClass *parent = name ? OSMetaClass::getMetaClassWithName(name) : nullptr;
    OSSafeReleaseNULL(name);
    if (!parent || parent->getClassSize() != 136) return fail(20, 2, "IOAcceleratorES base");
    void *handle = OSMetaClass::preModLoad(g_kernel_kmod_info.name);
    if (!handle) return fail(20, 3, "preModLoad");
    native_adreno_accelerator_static_init();
    if (OSReturn r = OSMetaClass::postModLoad(handle)) return fail(20, r, "postModLoad");
    if (native_adreno_accelerator_instance_size() != 136) return fail(20, 4, "accelerator size");
    IOService *accelerator = native_adreno_accelerator_allocate();
    if (!accelerator || !accelerator->init()) {if (accelerator) accelerator->release(); return fail(20, 5, "accelerator init");}
    accelerator->setProperty("MetalPluginName", "NativeAdrenoMetal");
    accelerator->setProperty("MetalPluginClassName", "NativeAdrenoMetalDevice");
    accelerator->setProperty("NativeSandboxGrantProcess", "backboardd,iconservicesagent,WidgetRenderer_Default,PosterBoard");
    if (!accelerator->attach(IOService::getPlatform())) {accelerator->release(); return fail(20, 6, "accelerator attach");}
    accelerator->registerService();
    stage(21, 0);
    IOLog("BluelineGpu: Metal accelerator registered\n");
    screen("Metal accelerator registered");
    return true;
}

void gpuThread(void *, wait_result_t) {
    statusStart();
    screen("display up, GPU starts in %u s", bootArg("blueline-gpu-delay-ms", 10000) / 1000);
    IOSleep(bootArg("blueline-gpu-delay-ms", 10000));
    const unsigned mode = bootArg("blueline-gpu", 0);
    IOLog("BluelineGpu: mode %u starting\n", mode);
    if (bringUp() && publish() && mode >= 2) publishMetal();
}
}   // namespace

extern "C" size_t blueline_gpu_status_text(char *out, size_t cap) {return statusText(out, cap);}

// Started once from the display driver's late probe, as sheng starts sheng_gpu_power_probe.
extern "C" void blueline_gpu_start(void) {
    static volatile uint32_t started;
    if (!bootArg("blueline-gpu", 0) || !__sync_bool_compare_and_swap(&started, 0, 1)) return;
    thread_t thread = nullptr;
    if (kernel_thread_start(gpuThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}
