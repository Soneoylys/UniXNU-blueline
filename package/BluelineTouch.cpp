// SPDX-License-Identifier: BSD-2-Clause-Patent
// Pixel 3 (blueline) touchscreen, ported from refs/pixel3-ios (erikkunz, iOS 16 on this phone): bsd/dev/blueline_touch.c as
// patches 0105, 0113, 0114 and 0121 leave it. Unchanged from there: the ST FTM5 ("st,fts", I2C 0x49) on bit-banged I2C over
// QUP wrapper 0 SE2's pins (GPIO 27 SDA, 28 SCL; the SE's FIFO interface is disabled and its GPI DMA path takes the SoC down),
// GPIO 136 high to give the bus to the AP, the PM8998 LDO19 (3.3 V) vote over RPMh looked up as "ldoa19" in the AOP's
// command database (apps RSC DRV 2, one active TCS), the FTM5 start-up of Google's fts_resume_work and the event decoding.
// Changed for UniXNU: contacts go to ShengHidInput's touch device (NativeHidInput's seqlocked native_touch_* set and its sink)
// instead of ios-sweet's SweetHIDTouch, in that device's units; the reference's interrupt mode (TLMM summary SPI 208) is
// not ported yet because ArmCpu has no SPI mask for the shared summary line, so the thread polls as 0113 did (every 10 ms;
// the FIFO is read when the attention line GPIO 125 is low, a finger is down, or every 100 ms).
// Boot argument bluetouch=0 turns it off.
#include <IOKit/IOLib.h>
#include <kern/clock.h>
#include <kern/thread.h>
#include <mach/mach_time.h>
#include <pexpert/pexpert.h>

extern "C" {
vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
boolean_t ml_set_interrupts_enabled(boolean_t);
// NativeHidInput's touch inputs: a seqlock (odd while writing), the down fingers as identifier and position. Positions are in
// the units NativeHidInput maps to 0..32767: 30480 across the panel's width, 20320 down its height.
volatile uint32_t native_touch_seq, native_touch_count, native_touch_hid_reports;
volatile int native_touch_xs[12], native_touch_ys[12], native_touch_ids[12];
// [0] stage (1 mapped, 2 chip answered, 3 started, 4 polling; negative: the step that failed), [1] chip id, [2] RPMh error,
// [3] start errors (reset | irq << 8 | sense << 16, ready bit 31), [4] events, [5] FIFO read errors, [6] down mask, [7] reports
volatile int32_t blueline_touch_state[8];
void native_touch_set_sink(void (*sink)(void *), void *context);
void blueline_touch_start(void);
}

namespace {
constexpr uint32_t kTlmmEast = 0x03500000u, kTlmmNorth = 0x03900000u;   // GPIO 27, 28, 125; GPIO 136
constexpr uint32_t kRscDrv = 0x179e0000u, kRscTcs = 0xd00u, kTcsStride = 0x2a0u;
constexpr uint32_t kCmdDbLocator = 0x0c3f000cu;                         // AOP message RAM: cmd-db address
constexpr int kSda = 27, kScl = 28, kAttention = 125, kSwitch = 136;
constexpr uint8_t kFtsAddress = 0x49;
constexpr unsigned kFingers = 10, kPanelWidth = 1080, kPanelHeight = 2160;
constexpr uint32_t gpioCfg(int n) {return 0x1000u * uint32_t(n);}
constexpr uint32_t gpioIo(int n) {return 0x1000u * uint32_t(n) + 4u;}

vm_offset_t gEast, gNorth, gRsc;
void (*volatile gSink)(void *);
void *volatile gSinkContext;

uint32_t rd(vm_offset_t a) {
    const uint32_t v = *reinterpret_cast<volatile uint32_t *>(a);
    __asm__ volatile("dmb oshld" ::: "memory");
    return v;
}
void wr(vm_offset_t a, uint32_t v) {
    __asm__ volatile("dmb oshst" ::: "memory");
    *reinterpret_cast<volatile uint32_t *>(a) = v;
}
uint8_t rd8(vm_offset_t a) {return *reinterpret_cast<volatile uint8_t *>(a);}

// ---- I2C, bit-banged ----
// Released: input with pull-up; low: output enabled, driving 0.
void pin(int n, int high) {wr(gEast + gpioCfg(n), (high ? 0u : (1u << 9)) | (2u << 6) | 3u);}
int level(int n) {return rd(gEast + gpioIo(n)) & 1;}
void sclHigh() {
    pin(kScl, 1);
    for (int i = 0; i < 1000 && !level(kScl); i++) IODelay(1);   // clock stretching, 1 ms at most
}
void start() {pin(kSda, 1); sclHigh(); IODelay(5); pin(kSda, 0); IODelay(5); pin(kScl, 0); IODelay(5);}
void stop() {pin(kSda, 0); IODelay(5); sclHigh(); IODelay(5); pin(kSda, 1); IODelay(5);}
bool writeByte(uint8_t b) {
    for (int i = 7; i >= 0; i--) {pin(kSda, b >> i & 1); IODelay(5); sclHigh(); IODelay(5); pin(kScl, 0);}
    pin(kSda, 1); IODelay(5); sclHigh();
    const int nack = level(kSda);
    IODelay(5); pin(kScl, 0);
    return !nack;
}
uint8_t readByte(bool ack) {
    uint8_t b = 0;
    pin(kSda, 1);
    for (int i = 0; i < 8; i++) {IODelay(5); sclHigh(); b = uint8_t(b << 1 | level(kSda)); IODelay(5); pin(kScl, 0);}
    pin(kSda, !ack); IODelay(5); sclHigh(); IODelay(5); pin(kScl, 0); pin(kSda, 1);
    return b;
}
// Write cmd, then (if len) a repeated start and read len bytes; interrupts masked for the transfer. True on success.
bool xfer(const uint8_t *cmd, uint32_t clen, uint8_t *out, uint32_t len) {
    const boolean_t interrupts = ml_set_interrupts_enabled(FALSE);
    start();
    bool ok = writeByte(kFtsAddress << 1);
    for (uint32_t i = 0; i < clen && ok; i++) ok = writeByte(cmd[i]);
    if (ok && len) {
        start();
        ok = writeByte(uint8_t(kFtsAddress << 1 | 1));
        for (uint32_t i = 0; i < len && ok; i++) out[i] = readByte(i + 1 < len);
    }
    stop();
    ml_set_interrupts_enabled(interrupts);
    return ok;
}

// ---- RPMh: LDO19 from the AOP ----
uint32_t cmdDbAddress(const char *id) {
    const vm_offset_t locator = ml_io_map(kCmdDbLocator & ~0xfffu, 0x1000);
    if (!locator) return 0;
    // The word after the address is not a usable size here: map SDM845's 128 KiB cmd-db region (0114).
    const uint32_t pa = rd(locator + (kCmdDbLocator & 0xfffu)), size = 0x20000;
    if (!pa) return 0;
    const vm_offset_t db = ml_io_map(pa, size);
    if (!db || rd(db + 4) != 0x0c0330dbu) return 0;   // magic db 30 03 0c
    for (uint32_t i = 0; i < 8; i++) {
        const vm_offset_t h = db + 8 + 16 * i;          // rsc_hdr: slv_id, header_offset, data_offset, cnt
        const uint32_t slave = rd8(h) | uint32_t(rd8(h + 1)) << 8;
        if (!slave) break;
        const uint32_t offset = rd8(h + 2) | uint32_t(rd8(h + 3)) << 8, count = rd8(h + 6) | uint32_t(rd8(h + 7)) << 8;
        vm_offset_t e = db + 144 + offset;              // entry_header: id[8], priority[2], addr, len, offset
        for (uint32_t j = 0; j < count && 144 + offset + 24 * (j + 1) <= size; j++, e += 24) {
            bool same = true;
            for (int k = 0; k < 8 && same; k++) {
                same = rd8(e + k) == uint8_t(id[k]);
                if (!id[k]) break;
            }
            if (same) return rd(e + 16);
        }
    }
    return 0;
}
// n commands (address, data) in an idle active TCS, triggered and waited for. 0 on success.
int rpmhWrite(const uint32_t *address, const uint32_t *data, int n) {
    const uint32_t stride = (rd(gRsc) >> 16 & 0xffu) >= 3 ? 24 : 20;
    int t = -1;
    for (int i = 0; i < 2 && t < 0; i++) {
        const vm_offset_t b = gRsc + kRscTcs + kTcsStride * uint32_t(i);
        if (rd(b + 0x18) && !rd(b + 0x14) && !rd(b + 0x1c)) t = i;   // idle, no control, no commands
    }
    if (t < 0) return 16;   // EBUSY
    const vm_offset_t b = gRsc + kRscTcs + kTcsStride * uint32_t(t);
    wr(gRsc + kRscTcs + 0x08, 1u << t);   // IRQ_CLEAR
    for (int i = 0; i < n; i++) {
        wr(b + 0x30 + stride * uint32_t(i), 8u | (1u << 16) | (1u << 8));   // write, response
        wr(b + 0x34 + stride * uint32_t(i), address[i]);
        wr(b + 0x38 + stride * uint32_t(i), data[i]);
    }
    wr(b + 0x1c, (1u << n) - 1);              // CMD_ENABLE
    wr(b + 0x14, 0);                          // CONTROL: AMC on, then trigger
    wr(b + 0x14, 1u << 16);
    wr(b + 0x14, (1u << 16) | (1u << 24));
    bool done = false;
    for (int us = 0; us < 50000 && !done; us++) {
        done = true;
        for (int i = 0; i < n; i++)
            if (!(rd(b + 0x3c + stride * uint32_t(i)) & (1u << 16))) done = false;   // completed
        IODelay(1);
    }
    wr(b + 0x1c, 0);
    wr(b + 0x14, 0);
    wr(gRsc + kRscTcs + 0x08, 1u << t);
    return done ? 0 : 60;   // ETIMEDOUT
}
int power() {
    const uint32_t a = cmdDbAddress("ldoa19");
    if (!a) {IOLog("BluelineTouch: ldoa19 not in cmd-db\n"); return 2;}
    // pmic4 pldo: 3.3 V, mode HPM (7), then enable
    const uint32_t address[3] = {a + 0, a + 8, a + 4}, data[3] = {3300, 7, 1};
    const int error = rpmhWrite(address, data, 3);
    IOLog("BluelineTouch: RPMh ldoa19 (%#x) 3300 mV HPM on: error %d\n", a, error);
    IODelay(20000);
    return error;
}

// ---- FTM5 ----
const uint8_t kFifo[1] = {0x86};
void ftsStart() {
    static const uint8_t reset[6] = {0xfa, 0x20, 0x00, 0x00, 0x24, 0x80};
    static const uint8_t interruptOn[6] = {0xfa, 0x20, 0x00, 0x00, 0x29, 0x41};
    static const uint8_t senseOn[3] = {0xa0, 0x00, 0xff};
    uint8_t e[8];
    bool ready = false;
    const bool e0 = xfer(reset, sizeof reset, nullptr, 0);
    for (int i = 0; i < 50 && !ready; i++) {IOSleep(10); ready = xfer(kFifo, 1, e, 8) && e[0] == 0x03;}
    const bool e1 = xfer(interruptOn, sizeof interruptOn, nullptr, 0), e2 = xfer(senseOn, sizeof senseOn, nullptr, 0);
    blueline_touch_state[3] = int32_t(uint32_t(!e0) | uint32_t(!e1) << 8 | uint32_t(!e2) << 16 | uint32_t(ready) << 31);
    IOLog("BluelineTouch: reset %s %s, interrupt %s, sense on %s\n", e0 ? "ok" : "error", ready ? "ready" : "NOT ready",
          e1 ? "ok" : "error", e2 ? "ok" : "error");
}

// Publishes the down fingers to NativeHidInput's seqlock and notifies its device.
void publish(uint32_t down, const int *x, const int *y) {
    unsigned n = 0;
    native_touch_seq = native_touch_seq + 1;
    __asm__ volatile("dmb ish" ::: "memory");
    for (unsigned id = 0; id < kFingers; id++) {
        if (!(down & (1u << id))) continue;
        native_touch_ids[n] = int(id);
        native_touch_xs[n] = int(uint64_t(x[id] < 0 ? 0 : x[id]) * 30480 / kPanelWidth);
        native_touch_ys[n] = int(uint64_t(y[id] < 0 ? 0 : y[id]) * 20320 / kPanelHeight);
        ++n;
    }
    native_touch_count = n;
    __asm__ volatile("dmb ish" ::: "memory");
    native_touch_seq = native_touch_seq + 1;
    blueline_touch_state[6] = int32_t(down);
    blueline_touch_state[7] = blueline_touch_state[7] + 1;
    void (*sink)(void *) = __atomic_load_n(&gSink, __ATOMIC_ACQUIRE);
    if (sink) sink(gSinkContext);
}

void touchThread(void *, wait_result_t) {
    blueline_touch_state[0] = 1;
    // Bus to the AP: switch GPIO 136 driven high. SDA/SCL as plain GPIOs, output value 0, released.
    wr(gNorth + gpioIo(kSwitch), 2u);
    wr(gNorth + gpioCfg(kSwitch), 1u << 9);
    wr(gEast + gpioIo(kSda), 0);
    wr(gEast + gpioIo(kScl), 0);
    pin(kSda, 1);
    pin(kScl, 1);
    IODelay(1000);
    for (int i = 0; i < 9; i++) {pin(kScl, 0); IODelay(5); sclHigh(); IODelay(5);}   // free a device stuck mid-byte
    stop();
    static const uint8_t idCommand[5] = {0xfa, 0x20, 0x00, 0x00, 0x00};
    uint8_t id[4] = {};
    if (!xfer(idCommand, sizeof idCommand, id, sizeof id)) {
        IOLog("BluelineTouch: no answer from FTS at 0x49; touch off\n");
        blueline_touch_state[0] = -2;
        return;
    }
    blueline_touch_state[1] = int32_t(uint32_t(id[0]) << 24 | uint32_t(id[1]) << 16 | uint32_t(id[2]) << 8 | id[3]);
    blueline_touch_state[0] = 2;
    IOLog("BluelineTouch: FTS chip id %02x %02x %02x %02x (bit-banged I2C)\n", id[0], id[1], id[2], id[3]);
    blueline_touch_state[2] = power();
    ftsStart();
    blueline_touch_state[0] = 4;
    uint8_t buf[8 * 32];
    uint32_t down = 0;
    int x[kFingers] = {}, y[kFingers] = {};
    uint64_t sent[kFingers] = {}, moveAbs = 0;
    unsigned ticks = 0;
    nanoseconds_to_absolutetime(16000000ull, &moveAbs);   // moves at most every 16 ms per finger; down and up always
    for (;;) {
        // Attention line low: events pending; otherwise only every 100 ms, or while a finger is down.
        if (!(rd(gEast + gpioIo(kAttention)) & 1) || down || (++ticks % 10u) == 0) {
            const bool ok = xfer(kFifo, 1, buf, 8);
            if (ok && buf[0] != 0) {
                unsigned more = buf[7] & 0x1fu;
                if (more > 31u || (more && !xfer(kFifo, 1, buf + 8, more * 8u))) more = 0;
                bool changed = false;
                for (unsigned e = 0; e <= more; e++) {
                    const uint8_t *ev = buf + 8 * e;
                    const unsigned kind = ev[0] >> 4, finger = ev[1] >> 4, type = ev[1] & 0xfu;
                    if ((ev[0] & 0xfu) != 3 || finger >= kFingers) continue;
                    blueline_touch_state[4] = blueline_touch_state[4] + 1;
                    if ((kind == 1 || kind == 2) && type != 0 && type != 3) {
                        x[finger] = ((ev[3] & 0xf) << 8) | ev[2];
                        y[finger] = (ev[4] << 4) | (ev[3] >> 4);
                        const uint64_t t = mach_absolute_time();
                        if (!(down & (1u << finger)) || t - sent[finger] >= moveAbs) {sent[finger] = t; changed = true;}
                        down |= 1u << finger;
                    } else if (kind == 3 && (down & (1u << finger))) {
                        down &= ~(1u << finger);
                        changed = true;
                    }
                }
                if (changed) publish(down, x, y);
            } else if (!ok) {
                blueline_touch_state[5] = blueline_touch_state[5] + 1;
            }
        }
        IOSleep(10);
    }
}
}   // namespace

extern "C" void native_touch_set_sink(void (*sink)(void *), void *context) {
    gSinkContext = context;
    __atomic_store_n(&gSink, sink, __ATOMIC_RELEASE);
}

// From blueline_buttons_start (after IOKitBSDInit). Everything else runs on the thread.
extern "C" void blueline_touch_start(void) {
    unsigned enabled = 1;
    PE_parse_boot_argn("bluetouch", &enabled, sizeof enabled);
    if (!enabled || blueline_touch_state[0]) return;
    gEast = ml_io_map(kTlmmEast, gpioCfg(kAttention + 1));
    gNorth = ml_io_map(kTlmmNorth, gpioCfg(kSwitch + 1));
    gRsc = ml_io_map(kRscDrv, 0x4000);
    if (!gEast || !gNorth || !gRsc) {blueline_touch_state[0] = -1; return;}
    thread_t thread = nullptr;
    if (kernel_thread_start(touchThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
    else blueline_touch_state[0] = -1;
}
