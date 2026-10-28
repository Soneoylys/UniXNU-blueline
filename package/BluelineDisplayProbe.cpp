// SPDX-License-Identifier: BSD-2-Clause-Patent
// Board side of GopFramebuffer's NativeLegacyDisplay on the Pixel 3, the blueline counterpart of UniXNU-sheng's
// package/LegacyDisplayProbe.cpp: the observation hooks the display driver calls, its early class registration
// (native_legacy_display_probe, from offline-component-load-probes.patch) and its late start after IOSurface
// (native_legacy_display_late_probe, from offline-legacy-start.patch). The display scans out the firmware's GOP
// framebuffer; frames reach it only through the GPU hand-off presenter (sheng-fb-gpu=1), and the build removes the
// display's CPU copy path (package/build-blueline-display.py). The late probe then starts the keys (BluelineButtons.cpp)
// and the GPU (BluelineGpu.cpp).
// Nothing is drawn by this file.
#include <IOKit/IOPlatformExpert.h>
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOUserClient.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSData.h>
#include <pexpert/pexpert.h>
#include <mach/kmod.h>

extern "C" {
extern kmod_info_t g_kernel_kmod_info;
void native_legacy_display_static_init(void) __asm__("__GLOBAL__sub_I_NativeLegacyDisplay.cpp");
void native_gop_presenter_static_init(void) __asm__("__GLOBAL__sub_I_NativeGOPPresenter.cpp");
IOService *native_legacy_display_allocate(void);
IOService *native_gop_presenter_create(void);
bool native_gop_gpu_offer(IOService *, void *display, IOMemoryDescriptor *, uint64_t stride, uint32_t width, uint32_t height);
void native_gop_presenter_set_source(IOService *presenter, IOService *nub);
kern_return_t OSKextLoadKextWithIdentifier(const char *);
void blueline_gpu_start(void);
void blueline_log_start(void);   // BluelineLog.c, sheng-log=1
void blueline_buttons_start(void);   // BluelineButtons.cpp, power and volume-down keys
// [0] stage, [1] metaclass status, [2] init, [3] probe, [4] start, [5] presenter, [6] swaps validated, [7] swaps refused,
// [8] GPU offers refused, [9] transfers, [10] last transfer status, [11] retirements
volatile uint32_t blueline_display_state[12];
volatile uint32_t native_legacy_guard_slot;
volatile uint64_t native_legacy_guard_object, native_legacy_guard_caller;
}

namespace {
IOService *gPendingDisplay, *gDisplay, *gNub, *gPresenter;
IORegistryEntry *gChosen;
unsigned arg(const char *name, unsigned fallback) {
    unsigned value = fallback;
    PE_parse_boot_argn(name, &value, sizeof value);
    return value;
}
void stage(uint32_t s) {blueline_display_state[0] = s;}
bool consoleUsable(PE_Video &video) {
    return !PE_current_console(&video) && video.v_baseAddr && video.v_width && video.v_height && video.v_depth == 32 &&
           !video.v_rotate && video.v_width <= 16384 && video.v_height <= 16384 && video.v_rowBytes >= video.v_width * 4 &&
           video.v_rowBytes <= UINT64_MAX / video.v_height;
}
bool publishWord(IORegistryEntry *entry, const char *key, uint32_t value) {
    if (OSData *old = OSDynamicCast(OSData, entry->getProperty(key))) {
        uint32_t word = 0;
        if (old->getLength() != 4) return false;
        memcpy(&word, old->getBytesNoCopy(), 4);
        return word == value;
    }
    OSData *data = OSData::withBytes(&value, sizeof value);
    const bool ok = data && entry->setProperty(key, data);
    OSSafeReleaseNULL(data);
    return ok;
}
}   // namespace

extern "C" {
// Optional sheng board services the shared sources weak-import; the static kernel link needs a definition. The UFS log
// writer (BluelineLog.c) has no export region, so the plugin's pipeline-cache pieces are refused; no direct DPU scanout
// (SM8550 only).
int boot_log_export_put(uint32_t, uint32_t, uint32_t, const void *, uint32_t) {return -1;}
uint32_t native_ufs_dpu_map(unsigned, const uint64_t *, unsigned) {return 0;}
volatile uint32_t native_ufs_dpu_state = 0;
// Observation hooks (GopFramebuffer NativeLegacyDisplay.cpp). Counters only; nothing changes a verdict.
bool native_legacy_prepare_scanout(void) {return true;}   // no boot progress renderer shares the framebuffer here
void native_legacy_pm_observed(unsigned stage, IOReturn status, unsigned long) {
    if (status) IOLog("BluelineDisplay: PM stage %u status 0x%x\n", stage, status);
}
void native_legacy_surface_rejected(uint64_t width, uint64_t height, uint32_t format, uint32_t planes, uint64_t stride,
                                    uint64_t allocation, uint64_t bytes) {
    IOLog("BluelineDisplay: surface refused %llux%llu format %08x planes %u stride %llu alloc %llu bytes %llu\n", width, height,
          format, planes, stride, allocation, bytes);
}
void native_legacy_frame_observed(unsigned, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t) {}
void native_legacy_frame_colors_observed(const uint32_t *, const uint64_t *, uint64_t) {}
void native_legacy_swap_stage(unsigned) {}
void native_legacy_vsync_observed(unsigned, unsigned) {}
void native_legacy_swap_validation_observed(bool accepted, unsigned reason, const void *, const void *) {
    if (accepted) blueline_display_state[6] = blueline_display_state[6] + 1;
    else {
        const uint32_t refused = blueline_display_state[7] + 1;
        blueline_display_state[7] = refused;
        if (refused <= 8) IOLog("BluelineDisplay: swap refused, reason %u\n", reason);
    }
}
void native_legacy_copy_timing_observed(uint64_t, uint64_t) {}
void native_legacy_transfer_observed(IOReturn status, const void *, uint64_t) {
    blueline_display_state[9] = blueline_display_state[9] + 1;
    blueline_display_state[10] = uint32_t(status);
}
bool native_legacy_gpu_offer(void *display, IOMemoryDescriptor *memory, uint64_t stride, uint32_t width, uint32_t height) {
    const bool offered = gPresenter && native_gop_gpu_offer(gPresenter, display, memory, stride, width, height);
    if (!offered) blueline_display_state[8] = blueline_display_state[8] + 1;
    return offered;
}
void native_legacy_gpu_fallback_observed(int status) {IOLog("BluelineDisplay: GPU frame failed %d\n", status);}
void native_legacy_retirement_observed(IOService *) {blueline_display_state[11] = blueline_display_state[11] + 1;}
// Why a swap-queue kick or retirement stopped (build-blueline-display.py edits): [reason] counts, [0] the last reason.
// 1 transfer off, 2 previous frame still transferring, 3 display powered off, 4 no scanout mapping, 5 not primary,
// 6 no pending swap, 7 pending swap not validated, 8 no IOSurface, 9 offered to the GPU, 10 retirement could not
// activate the pending swap, 11 GPU completion without a frame.
volatile uint32_t blueline_kick_state[12];
void native_legacy_kick_observed(unsigned reason) {
    if (reason < 12) blueline_kick_state[reason] = blueline_kick_state[reason] + 1;
    blueline_kick_state[0] = reason;
}
void native_legacy_registration_observed(IOService *) {IOLog("BluelineDisplay: display registered\n");}
void native_legacy_client_enter(task_t, uint32_t) {}
void native_legacy_client_observed(IOReturn status, IOUserClient *client) {
    IOLog("BluelineDisplay: client %s status 0x%x\n", client ? client->getMetaClass()->getClassName() : "-", status);
}
// The display-scale policy: logical points = scanout pixels / scale. 3 makes the 1080x2160 panel a 360x720-point
// iPhone-sized screen (offline-legacy-unit-scale, as sheng uses 2 for its tablet panel).
bool native_legacy_gop_mapping_observed(IOService *, IOMemoryMap *mapping, uint32_t width, uint32_t height) {
    const unsigned scale = arg("offline-legacy-unit-scale", 0);
    if (!scale) return true;
    PE_Video video = {};
    if (scale > 3 || !consoleUsable(video) || video.v_width != width || video.v_height != height ||
        mapping->getPhysicalAddress() != video.v_baseAddr)
        return false;
    if (!gChosen) gChosen = IORegistryEntry::fromPath("/chosen", gIODTPlane);
    return gChosen && publishWord(gChosen, "display-scale", scale) && publishWord(gChosen, "display-rotation", 0);
}
__attribute__((noreturn, noinline)) void native_legacy_backend_guard(unsigned slot, void *object, void *caller) {
    native_legacy_guard_slot = slot;
    native_legacy_guard_object = uintptr_t(object);
    native_legacy_guard_caller = uintptr_t(caller);
    panic("NativeLegacyDisplay: leaf=%u caller=%llx stage=%u", slot, uint64_t(uintptr_t(caller)), blueline_display_state[0]);
}

// Early registration: the display and presenter classes join the kernel's metaclasses once, then the display object
// waits for the IOSurface resource.
void native_legacy_display_probe(void) {
    blueline_log_start();
    if (blueline_display_state[0]) return;
    stage(1);
    void *handle = OSMetaClass::preModLoad(g_kernel_kmod_info.name);
    if (!handle) return;
    native_legacy_display_static_init();
    native_gop_presenter_static_init();
    const OSReturn meta = OSMetaClass::postModLoad(handle);
    blueline_display_state[1] = uint32_t(meta);
    if (meta) return;
    PE_Video video = {};
    if (!consoleUsable(video)) return;
    IOService *display = native_legacy_display_allocate();
    if (!display) return;
    stage(3);
    blueline_display_state[2] = display->init();
    if (blueline_display_state[2]) gPendingDisplay = display;
    else display->release();
}

// After IOKitBSDInit: attach the display to a nub carrying the GOP framebuffer, start it, create the GPU hand-off
// presenter, then start the GPU, which consumes the presenter's frames.
void native_legacy_display_late_probe(void) {
    IOService *display = gPendingDisplay;
    if (!display) return;
    gPendingDisplay = nullptr;
    stage(4);
    const kern_return_t surfaceKext = OSKextLoadKextWithIdentifier("com.apple.iokit.IOSurface");
    OSDictionary *matching = IOService::nameMatching("IOCoreSurfaceRoot");
    IOService *surface = matching ? IOService::waitForMatchingService(matching, 5000000000ULL) : nullptr;
    const bool haveSurface = surface;
    OSSafeReleaseNULL(surface);
    OSSafeReleaseNULL(matching);
    PE_Video video = {};
    IOPlatformDevice *nub = nullptr;
    IODeviceMemory *memory = nullptr;
    OSArray *ranges = nullptr;
    IORegistryEntry *armIo = nullptr;
    IOService *parent = IOService::getResourceService();
    bool nubAttached = false, nubStarted = false, attached = false, started = false, treeAttached = false;
    if (!surfaceKext && haveSurface && consoleUsable(video) && parent) {
        nub = new IOPlatformDevice;
        memory = IODeviceMemory::withRange(video.v_baseAddr, video.v_rowBytes * video.v_height);
        ranges = OSArray::withCapacity(1);
        if (nub && memory && ranges && nub->init()) {
            ranges->setObject(memory);
            nub->setDeviceMemory(ranges);
            nub->setName("NativeGOPFramebuffer");
            nubAttached = nub->attach(parent);
            nubStarted = nubAttached && nub->start(parent);
        }
        if (nubStarted) {
            // The original display code finds its display under /arm-io as a display-subsystem node.
            armIo = IORegistryEntry::fromPath("/arm-io", gIODTPlane);
            const char type[] = "display-subsystem";
            OSData *data = OSData::withBytes(type, sizeof type);
            if (armIo && data && nub->setProperty("device_type", data)) {
                nub->setName("gop-display", gIODTPlane);
                treeAttached = nub->attachToParent(armIo, gIODTPlane);
            }
            OSSafeReleaseNULL(data);
            SInt32 score = 0;
            blueline_display_state[3] = treeAttached && display->probe(nub, &score) == display;
            attached = blueline_display_state[3] && display->attach(nub);
            stage(5);
            started = attached && display->start(nub);
            blueline_display_state[4] = started;
        }
    }
    if (!started) {
        IOLog("BluelineDisplay: display did not start (surface kext %d, IOSurface %d, nub %d/%d, tree %d, attach %d)\n",
              surfaceKext, haveSurface, nubAttached, nubStarted, treeAttached, attached);
        if (attached) display->detach(nub);
        if (treeAttached) nub->detachFromParent(armIo, gIODTPlane);
        if (nubStarted) nub->stop(parent);
        if (nubAttached) nub->detach(parent);
        OSSafeReleaseNULL(ranges);
        OSSafeReleaseNULL(memory);
        OSSafeReleaseNULL(nub);
        OSSafeReleaseNULL(armIo);
        display->release();
        stage(7);
        return;
    }
    gDisplay = display;
    gNub = nub;
    OSSafeReleaseNULL(armIo);
    stage(10);
    IOLog("BluelineDisplay: GOP display %ux%u started\n", unsigned(video.v_width), unsigned(video.v_height));
    // GPU hand-off presenter: a resource child, not a child of the display node (sheng int-075/077: the original display
    // enumeration crashed SpringBoard with the presenter under it).
    if (arg("sheng-fb-gpu", 0) == 1) {
        IOService *handoff = native_gop_presenter_create();
        bool handoffAttached = false;
        if (handoff) native_gop_presenter_set_source(handoff, nub);
        if (handoff && handoff->init() && (handoffAttached = handoff->attach(parent)) && handoff->start(parent)) {
            gPresenter = handoff;
            blueline_display_state[5] = 1;
        } else {
            if (handoffAttached) handoff->detach(parent);
            OSSafeReleaseNULL(handoff);
            IOLog("BluelineDisplay: GPU hand-off presenter did not start\n");
        }
    }
    blueline_buttons_start();
    blueline_gpu_start();
}
}
