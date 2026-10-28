// SPDX-License-Identifier: BSD-2-Clause-Patent
// Power and volume-down keys of the Pixel 3 (blueline) for iOS, sent as the Consumer Power and Volume Decrement bits of
// ShengHidInput's report 2 (NativeHidInput, built for 22H384 by package/build-blueline-hid.py): the device sits on the
// original IOHIDFamily, whose event driver hands the keys to backboardd (power wakes and sleeps the display).
// The keys are the PM8998 PON real-time status (SID 0, peripheral 0x08, register 0x10: bit 0 KPDPWR, bit 1 RESIN), read
// every 20 ms through this EE's observer channel of the SDM845 PMIC arbiter v5 (spmi@c440000: core 0xc440000, obsrvr
// 0xe600000, cnfg 0xc40a000), as bluelinePkg's BluelineKeyInputDxe and pixel3-ios 0104-blueline-keys.patch read them.
// Read-only: the only writes are EXT_READL commands to the observer channel. No PMIC register is written, no interrupt is
// configured or acknowledged. Volume up (PM8998 GPIO 6) does not follow the key as ABL leaves the pin, so it is not read.
// Boot argument blueline-keys=0 turns all of this off.
#include <IOKit/IOLib.h>
#include <IOKit/IOService.h>
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSSymbol.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <mach/kmod.h>

extern "C" {
extern kmod_info_t g_kernel_kmod_info;
void native_hid_static_init(void) __asm__("__GLOBAL__sub_I_NativeHidInput.cpp");
IOService *native_hid_input_allocate(void);
kern_return_t OSKextLoadKextWithIdentifier(const char *);
// NativeHidInput's touch inputs. The board has no touch driver yet; without sheng-touch-hid=1 the device publishes no
// digitizer collection and never reads these.
volatile uint32_t native_touch_seq, native_touch_count, native_touch_hid_reports;
volatile int native_touch_xs[12], native_touch_ys[12], native_touch_ids[12];
// The kernel links statically, so NativeHidInput's weak reference still needs a definition: no touch producer to notify.
void native_touch_set_sink(void (*)(void *), void *) {}
// [0] stage (1 arbiter, 2 HID kexts, 3 IOHIDResource, 4 class registered, 5 device started, 6 polling; negative: the step that
// failed), [1] arbiter version, [2] PON APID, [3] IOHIDFamily load, [4] IOHIDEventDriver load, [5] key changes, [6] pressed mask,
// [7] read errors
volatile int32_t blueline_keys_state[8];
// NativeHidInput's observations: [event] count and last result (2 started, 3 tick, 4 button report).
volatile uint32_t native_hid_input_events[8];
volatile int32_t native_hid_input_results[8];
void native_hid_input_observed(unsigned event, int result) {
    if (event < 8) {native_hid_input_events[event] = native_hid_input_events[event] + 1; native_hid_input_results[event] = result;}
}
void native_hid_live_checkpoint(IOService *) {}
// No unattended Home presses on this board.
bool native_hid_input_requested(void) {return false;}
void native_buttons_set_sink(void (*sink)(void *, uint32_t), void *context);
void blueline_buttons_start(void);
}

namespace {
constexpr uint64_t kCore = 0xc440000, kObsrvr = 0xe600000, kCnfg = 0xc40a000;
constexpr uint32_t kPonPpid = 0x008, kNoApid = 0xffff;
constexpr uint8_t kPonRtSts = 0x10, kPowerBit = 1u << 0, kResinBit = 1u << 1;
// Report 2 bits (NativeHidInput): 1 Power, 2 Volume Increment, 3 Volume Decrement.
constexpr uint32_t kReportPower = 1u << 1, kReportVolumeDown = 1u << 3;

struct Window {IOMemoryMap *map; volatile uint32_t *va;};
Window gCore, gObs, gCnfg;
uint32_t gPon = kNoApid;
void (*volatile gSink)(void *, uint32_t);
void *volatile gSinkContext;

void fail(int step) {blueline_keys_state[0] = -step;}
bool mapWindow(Window &w, uint64_t base, uint64_t size) {
    IODeviceMemory *memory = IODeviceMemory::withRange(base, size);
    w.map = memory ? memory->map(kIOMapAnywhere | kIOMapInhibitCache) : nullptr;
    if (memory) memory->release();
    w.va = w.map ? reinterpret_cast<volatile uint32_t *>(w.map->getVirtualAddress()) : nullptr;
    return w.va != nullptr;
}
uint32_t rd(const Window &w, uint32_t offset) {
    const uint32_t value = w.va[offset / 4];
    __asm__ volatile("dmb oshld" ::: "memory");
    return value;
}
// A PPID can be mapped more than once; prefer the entry this EE (0) owns.
uint32_t findApid(uint32_t ppid, uint32_t count) {
    uint32_t found = kNoApid;
    for (uint32_t i = 0; i < count; ++i) {
        const uint32_t map = rd(gCore, 0x900 + 4 * i);
        if (!map || ((map >> 8) & 0xfff) != ppid) continue;
        if (found == kNoApid || (rd(gCnfg, 0x700 + 4 * i) & 7) == 0) found = i;
    }
    return found;
}
// One-byte EXT_READL on the observer channel of apid (EE 0): command at +0, status at +8 (bit 0 done, bits 1..3 failure),
// data at +0x18.
bool readByte(uint32_t apid, uint8_t address, uint8_t *out) {
    volatile uint32_t *channel = gObs.va + (0x80 * apid) / 4;
    __asm__ volatile("dmb oshst" ::: "memory");
    channel[0] = (1u << 27) | (uint32_t(address) << 4);
    __asm__ volatile("dsb osh" ::: "memory");
    for (unsigned i = 0; i < 1000; ++i) {
        const uint32_t status = channel[2];
        if (status & 1) {
            if (status & 0xe) return false;
            *out = uint8_t(channel[6]);
            return true;
        }
        IODelay(1);
    }
    return false;
}

// The original HID family, its event driver, then NativeHidInput on the IOHIDResource service, as UniXNU-sheng's
// HidInputProbe.cpp starts it.
bool startHidDevice() {
    blueline_keys_state[0] = 2;
    blueline_keys_state[3] = OSKextLoadKextWithIdentifier("com.apple.iokit.IOHIDFamily");
    blueline_keys_state[4] = OSKextLoadKextWithIdentifier("com.apple.iokit.IOHIDEventDriver");
    if (blueline_keys_state[3]) {fail(2); return false;}
    OSDictionary *match = IOService::serviceMatching("IOHIDResource");
    IOService *resource = match ? IOService::waitForMatchingService(match, 60000000000ULL) : nullptr;
    OSSafeReleaseNULL(match);
    if (!resource) {fail(3); return false;}
    blueline_keys_state[0] = 3;
    // The device class is built for the 22H384 IOHIDDevice (208 bytes); refuse any other.
    const OSSymbol *name = OSSymbol::withCString("IOHIDDevice");
    const OSMetaClass *meta = name ? OSMetaClass::getMetaClassWithName(name) : nullptr;
    OSSafeReleaseNULL(name);
    void *handle = meta && meta->getClassSize() == 0xd0 ? OSMetaClass::preModLoad(g_kernel_kmod_info.name) : nullptr;
    if (!handle) {resource->release(); fail(4); return false;}
    native_hid_static_init();
    if (OSMetaClass::postModLoad(handle)) {resource->release(); fail(4); return false;}
    blueline_keys_state[0] = 4;
    IOService *device = native_hid_input_allocate();
    const bool valid = device && device->metaCast("IOHIDDevice") && device->init();
    const bool attached = valid && device->attach(resource);
    const bool started = attached && device->start(resource);
    if (!started) {
        if (attached) device->detach(resource);
        OSSafeReleaseNULL(device);
        resource->release();
        fail(5);
        return false;
    }
    resource->release();
    blueline_keys_state[0] = 5;
    IOLog("BluelineButtons: HID device started\n");
    return true;
}

void buttonsThread(void *, wait_result_t) {
    const uint32_t version = rd(gCore, 0), count = rd(gCore, 4) & 0x7ff;
    blueline_keys_state[1] = int32_t(version);
    if (version < 0x50000000u || version >= 0x70000000u || !count || count > 512) {fail(1); return;}
    gPon = findApid(kPonPpid, count);
    blueline_keys_state[2] = int32_t(gPon);
    IOLog("BluelineButtons: arbiter %#x, %u APIDs, PON APID %u\n", version, count, gPon);
    if (gPon == kNoApid) {fail(1); return;}
    const bool hid = startHidDevice();
    if (hid) blueline_keys_state[0] = 6;
    uint32_t pressed = 0;
    for (;;) {
        uint8_t status = 0;
        if (!readByte(gPon, kPonRtSts, &status)) {
            blueline_keys_state[7] = blueline_keys_state[7] + 1;
        } else {
            const uint32_t now = (status & kPowerBit ? kReportPower : 0) | (status & kResinBit ? kReportVolumeDown : 0);
            if (now != pressed) {
                pressed = now;
                blueline_keys_state[5] = blueline_keys_state[5] + 1;
                blueline_keys_state[6] = int32_t(now);
                IOLog("BluelineButtons: keys %#x\n", now);
                void (*sink)(void *, uint32_t) = __atomic_load_n(&gSink, __ATOMIC_ACQUIRE);
                if (sink) sink(gSinkContext, now);
            }
        }
        IOSleep(20);
    }
}
}   // namespace

extern "C" void native_buttons_set_sink(void (*sink)(void *, uint32_t), void *context) {
    gSinkContext = context;
    __atomic_store_n(&gSink, sink, __ATOMIC_RELEASE);
}

// From native_legacy_display_late_probe (after IOKitBSDInit). Everything else runs on the thread.
extern "C" void blueline_buttons_start(void) {
    unsigned enabled = 1;
    PE_parse_boot_argn("blueline-keys", &enabled, sizeof enabled);
    if (!enabled || blueline_keys_state[0]) return;
    blueline_keys_state[0] = 1;
    if (!mapWindow(gCore, kCore, 0x2000) || !mapWindow(gCnfg, kCnfg, 0x1000) || !mapWindow(gObs, kObsrvr, 0x10000)) {fail(1); return;}
    thread_t thread = nullptr;
    if (kernel_thread_start(buttonsThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
    else fail(1);
}
