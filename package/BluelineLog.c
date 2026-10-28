// SPDX-License-Identifier: MIT
// Diagnostic log on UFS for blueline (boot argument sheng-log=1), ported from fajita/sheng package/BootLog.c with the same window
// format (header "SHLOG001" version 4), so scripts/blueline-log.py and sheng's read-sheng-log.py --decode read it. Every 2 s, and
// on demand from the GPU bring-up (blueline_log_checkpoint, before each step that can hang the bus), it writes a text slot (the GPU
// and display state words and the on-screen status rows) and the kernel message buffer to the last 32 MiB of the darwin partition
// through NativeUfs's bounded log window, then the header that makes both visible. Read it from TWRP with one dd
// (scripts/blueline-dump.ps1). Left out of the fajita writer: the pipeline-cache and diagnostic-file exports, the board page,
// the reset-to-fastboot path, and the panic flush (blueline's panic path has no hook yet).
//
// Window layout (4096-byte blocks): 0 header, 1..32 the kernel message buffer (oldest first), 33.. text slots of 16 blocks in a ring.
#include <kern/thread.h>
#include <kern/clock.h>
#include <string.h>
#include <pexpert/pexpert.h>
#include <IOKit/IOLib.h>

extern int native_ufs_log_open(uint64_t *, uint64_t *, const char **) __attribute__((weak_import));
extern int native_ufs_log_write(uint64_t, uint32_t, const void *) __attribute__((weak_import));
extern int native_ufs_log_sync(void) __attribute__((weak_import));
extern unsigned native_ufs_store_gpt_check(void) __attribute__((weak_import));
extern volatile uint32_t native_data_persist_state __attribute__((weak_import));
extern volatile uint64_t native_data_persist_writes __attribute__((weak_import));
extern volatile uint64_t native_data_persist_errors __attribute__((weak_import));
extern volatile uint32_t blueline_gpu_state[8] __attribute__((weak_import));
extern volatile uint32_t blueline_display_state[12] __attribute__((weak_import));
extern volatile uint64_t native_gop_gpu_completions __attribute__((weak_import));
extern volatile int32_t blueline_keys_state[8] __attribute__((weak_import));
extern volatile int32_t blueline_touch_state[8] __attribute__((weak_import));
extern volatile uint32_t blueline_kick_state[12] __attribute__((weak_import));
// ArmCpu (drivers/ArmCpu): per logical core architectural timer interrupts and IPI SGIs (armcpu-ipi-sgi) taken, IPIs sent.
extern volatile uint32_t armcpu_core_timer[8] __attribute__((weak_import));
extern volatile uint32_t armcpu_core_sgi[8] __attribute__((weak_import));
extern volatile uint64_t armcpu_ipi_sent __attribute__((weak_import));
extern volatile uint32_t armcpu_gic_boot[8] __attribute__((weak_import));
extern volatile uint32_t native_hid_input_events[8] __attribute__((weak_import));
extern volatile int32_t native_hid_input_results[8] __attribute__((weak_import));
extern size_t blueline_gpu_status_text(char *, size_t) __attribute__((weak_import));
// What the Metal plugin told the driver (UniXNU drivers/Adreno740/NativeAdrenoPublish.cpp; same layouts): its progress markers (open types
// 0x4147xxxx, each also a sandbox "deny iokit-open-user-client" line in the system log), its result lines, other processes' failure lines,
// the per-process open records and the bridge's telemetry rows.
struct NativeAdrenoMetalDiag {
  uint32_t opens, busy, bad, closes, reports, property_reads, foreign_opens, foreign_type, name_reads, calls, last_selector, last_status, ext_entries,
      ext_selector, ext_status, ext_in, ext_out, tunnel_calls, probes, sel_bits[8], mb_maps, mb_calls, marks_total, marks[16], chk_macf, chk_res,
      chk_type, type_pass_lo, type_pass_hi, attach_status, inflight, inflight_since_calls, forced_unmaps;
  char opener[3][17], reader[3][17], name_reader[17], starter[3][17], report[64], report2[64];
  uint32_t grants;
};
struct NativeAdrenoClientText {char name[20]; char text[64];};
struct NativeAdrenoClientRecord {char name[20]; uint32_t opens, refused, closes, slot, peak_mib, last_refusal;};
extern struct NativeAdrenoMetalDiag native_adreno_metal_diag __attribute__((weak_import));
extern char native_adreno_metal_rows[48][96] __attribute__((weak_import));
extern struct NativeAdrenoClientText native_adreno_client_text[4] __attribute__((weak_import));
extern struct NativeAdrenoClientRecord native_adreno_client_log[8] __attribute__((weak_import));

int blueline_log_checkpoint(const char *why);
void blueline_log_start(void);

// The kernel message buffer (bsd/sys/msgbuf.h); only these leading fields are read.
struct boot_log_msgbuf {int msg_magic, msg_size, msg_bufx, msg_bufr; char *msg_bufc;};
extern struct boot_log_msgbuf *msgbufp;

enum {LogBlock = 4096, LogMsgFirst = 1, LogMsgBlocks = 32, LogSlotFirst = 33, LogSlotBlocks = 16, LogPeriodMs = 2000, LogMaxErrors = 3};
struct boot_log_header {
  char magic[8];                 // "SHLOG001"
  uint32_t version, header_bytes;
  uint64_t boot_id;              // CNTPCT when the writer started: tells one run from the next
  uint64_t first_lba;            // absolute 4096-byte LBA of block 0 on LUN 0
  uint32_t window_blocks, msg_first, msg_blocks, msg_bytes, slot_first, slot_blocks, slot_count, slots_written;
  uint32_t uptime_s, rounds, write_errors, gpt_open, gpt_last, state;
  char tag[32];
  uint32_t export_first, export_blocks, export_bytes, export_generation;   // version 2/3: no export on blueline (all 0)
  uint32_t export_below;
  uint32_t diag_first, diag_bytes;                                         // version 4: none
};
// Writer state: 0 off, 1 waiting for the window, 2 writing, 3 stopped after errors, 4 stopped (GPT changed).
volatile uint32_t boot_log_state, boot_log_rounds, boot_log_errors;
volatile uint64_t boot_log_first_lba;
static struct boot_log_header boot_log_h;
static uint8_t *boot_log_buffer;   // LogMsgBlocks blocks: the message buffer, then reused for a slot and the header
static IOLock *boot_log_lock;      // one round at a time: the periodic writer or a checkpoint
static uint64_t boot_log_start_ticks, boot_log_freq;
static uint32_t boot_log_failures;

static uint64_t log_ticks(void) {uint64_t v; __asm__ volatile("isb\n\tmrs %0, cntpct_el0" : "=r"(v)); return v;}

// The message buffer, oldest byte first, NUL-padded to LogMsgBlocks blocks; returns the bytes copied.
static uint32_t copy_msgbuf(uint8_t *out) {
  memset(out, 0, LogMsgBlocks * LogBlock);
  struct boot_log_msgbuf *m = msgbufp;
  if (!m || !m->msg_bufc || m->msg_size <= 0) return 0;
  int size = m->msg_size, x = m->msg_bufx;
  if (x < 0 || x >= size) x = 0;
  uint32_t n = 0;
  for (int i = 0; i < size && n < LogMsgBlocks * LogBlock; ++i) {
    char c = m->msg_bufc[(x + i) % size];
    if (c) out[n++] = (uint8_t)c;
  }
  return n;
}

static void compose(char *out, size_t cap, const char *why, unsigned now) {
  size_t at = 0;
#define ADD(...) do { int n_ = snprintf(out + at, cap - at, __VA_ARGS__); if (n_ > 0) at += (size_t)n_ < cap - at ? (size_t)n_ : cap - at - 1; } while (0)
  ADD("[blueline] slot %u round %u uptime %us (%s)\n", boot_log_h.slots_written, boot_log_h.rounds, now, why);
  if (&blueline_gpu_state)
    ADD("[gpu] stage %u status 0x%x phase %u window %u offset 0x%x value 0x%x\n", blueline_gpu_state[0], blueline_gpu_state[1],
        blueline_gpu_state[2], blueline_gpu_state[3], blueline_gpu_state[4], blueline_gpu_state[5]);
  if (&blueline_display_state) {
    ADD("[display]");
    for (unsigned i = 0; i < 12; ++i) ADD(" %x", blueline_display_state[i]);
    ADD("\n");
  }
  if (&native_gop_gpu_completions) ADD("[gop] gpu completions %llu\n", (unsigned long long)native_gop_gpu_completions);
  if (&armcpu_core_timer && &armcpu_core_sgi && &armcpu_ipi_sent) {
    ADD("[cpu] ipi sent %llu | timer", (unsigned long long)armcpu_ipi_sent);
    for (unsigned i = 0; i < 8; ++i) ADD(" %u", armcpu_core_timer[i]);
    ADD(" | sgi");
    for (unsigned i = 0; i < 8; ++i) ADD(" %u", armcpu_core_sgi[i]);
    ADD("\n");
  }
  // ArmCpu's boot-core GIC readback: IGROUPR0, ISENABLER0, GICD_CTLR, GICD_TYPER, IGRPMODR0, ICC_PMR, ICC_IGRPEN1, ICC_SRE.
  if (&armcpu_gic_boot) {
    ADD("[gic]");
    for (unsigned i = 0; i < 8; ++i) ADD(" %x", armcpu_gic_boot[i]);
    ADD("\n");
  }
  if (&blueline_touch_state)
    ADD("[touch] stage %d id %08x rpmh %d start %08x events %d errors %d down %x reports %d\n", blueline_touch_state[0],
        blueline_touch_state[1], blueline_touch_state[2], blueline_touch_state[3], blueline_touch_state[4], blueline_touch_state[5],
        blueline_touch_state[6], blueline_touch_state[7]);
  if (&blueline_kick_state) {
    ADD("[kick] last %u |", blueline_kick_state[0]);
    for (unsigned i = 1; i < 12; ++i) ADD(" %u", blueline_kick_state[i]);
    ADD("\n");
  }
  if (&blueline_keys_state && &native_hid_input_events && &native_hid_input_results)
    ADD("[keys] stage %d arbiter %x apid %d kexts %x/%x changes %d mask %x errors %d | hid started %u reports %u last %x\n",
        blueline_keys_state[0], blueline_keys_state[1], blueline_keys_state[2], blueline_keys_state[3], blueline_keys_state[4],
        blueline_keys_state[5], blueline_keys_state[6], blueline_keys_state[7], native_hid_input_events[2],
        native_hid_input_events[4], native_hid_input_results[4]);
  if (&native_data_persist_state)
    ADD("[data] state %u writes %llu errors %llu\n", native_data_persist_state,
        &native_data_persist_writes ? (unsigned long long)native_data_persist_writes : 0ull,
        &native_data_persist_errors ? (unsigned long long)native_data_persist_errors : 0ull);
  if (&native_adreno_metal_diag) {
    const struct NativeAdrenoMetalDiag *d = &native_adreno_metal_diag;
    ADD("[plugin] opens %u busy %u bad %u closes %u calls %u last sel %u status 0x%x grants %u marks %u:", d->opens, d->busy, d->bad,
        d->closes, d->calls, d->last_selector, d->last_status, d->grants, d->marks_total);
    const uint32_t total = d->marks_total, shown = total < 16 ? total : 16;
    for (uint32_t i = total - shown; i < total; ++i) ADD(" %x", d->marks[i & 15]);
    ADD("\n[plugin] starter %.17s %.17s opener %.17s %.17s name_reader %.17s\n", d->starter[0], d->starter[1], d->opener[0], d->opener[1],
        d->name_reader);
    ADD("[plugin] report %.64s\n[plugin] report2 %.64s\n", d->report, d->report2);
  }
  if (&native_adreno_client_text)
    for (unsigned i = 0; i < 4; ++i)
      if (native_adreno_client_text[i].name[0])
        ADD("[client] %.20s: %.64s\n", native_adreno_client_text[i].name, native_adreno_client_text[i].text);
  if (&native_adreno_client_log)
    for (unsigned i = 0; i < 8; ++i)
      if (native_adreno_client_log[i].name[0]) {
        const struct NativeAdrenoClientRecord *c = &native_adreno_client_log[i];
        ADD("[opens] %.20s opens %u refused %u closes %u slot %u peak %u MiB last refusal 0x%x\n", c->name, c->opens, c->refused, c->closes,
            c->slot, c->peak_mib, c->last_refusal);
      }
  if (&native_adreno_metal_rows)
    for (unsigned i = 0; i < 48; ++i)
      if (native_adreno_metal_rows[i][0]) ADD("[row %u] %.96s\n", i, native_adreno_metal_rows[i]);
  if (&blueline_gpu_status_text && at + 2 < cap) {
    ADD("[status]\n");
    at += blueline_gpu_status_text(out + at, cap - at);
  }
#undef ADD
}

// One round: text slot, then the message buffer, then the header that makes both visible, then a cache sync. Lock held.
static int round_locked(const char *why) {
  const unsigned now = (unsigned)((log_ticks() - boot_log_start_ticks) / boot_log_freq);
  int ok = 1;
  memset(boot_log_buffer, 0, LogSlotBlocks * LogBlock);
  compose((char *)boot_log_buffer, LogSlotBlocks * LogBlock, why, now);
  ok &= native_ufs_log_write(LogSlotFirst + (uint64_t)(boot_log_h.slots_written % boot_log_h.slot_count) * LogSlotBlocks, LogSlotBlocks,
                             boot_log_buffer);
  boot_log_h.msg_bytes = copy_msgbuf(boot_log_buffer);
  ok &= native_ufs_log_write(LogMsgFirst, LogMsgBlocks, boot_log_buffer);
  if (ok) ++boot_log_h.slots_written;
  else ++boot_log_h.write_errors;
  boot_log_h.uptime_s = now; boot_log_h.rounds = ++boot_log_rounds; boot_log_h.state = boot_log_state;
  if (&native_ufs_store_gpt_check && !(boot_log_h.rounds % 30)) boot_log_h.gpt_last = native_ufs_store_gpt_check();
  memset(boot_log_buffer, 0, LogBlock);
  memcpy(boot_log_buffer, &boot_log_h, sizeof boot_log_h);
  ok &= native_ufs_log_write(0, 1, boot_log_buffer);
  ok &= native_ufs_log_sync();
  if (!ok) boot_log_errors = ++boot_log_failures;
  else boot_log_failures = 0;
  if (boot_log_failures >= LogMaxErrors) boot_log_state = 3;
  if (boot_log_h.gpt_last != boot_log_h.gpt_open) boot_log_state = 4;
  return ok;
}

// A synchronous round from any thread (the GPU bring-up calls it before steps that can stall the bus): when it returns, the
// message buffer and status up to now are on the medium. No-op until the writer has opened the window.
int blueline_log_checkpoint(const char *why) {
  if (boot_log_state != 2 || !boot_log_lock) return 0;
  IOLockLock(boot_log_lock);
  const int ok = boot_log_state == 2 ? round_locked(why ? why : "checkpoint") : 0;
  IOLockUnlock(boot_log_lock);
  return ok;
}

static void writer(void *arg, wait_result_t result) {
  (void)arg; (void)result;
  thread_set_thread_name(current_thread(), "boot_log");
  uint64_t first = 0, blocks = 0;
  const char *why = "";
  boot_log_state = 1;
  // The window exists once NativeUfs has read the root layout; until then the open is refused ("no root layout").
  while (!native_ufs_log_open(&first, &blocks, &why)) {
    if (strcmp(why, "no root layout")) {
      IOLog("BluelineLog: window refused: %s\n", why);
      boot_log_state = 3;
      return;
    }
    IOSleep(1000);
  }
  boot_log_first_lba = first;
  struct boot_log_header *h = &boot_log_h;
  memset(h, 0, sizeof *h);
  memcpy(h->magic, "SHLOG001", 8);
  h->version = 4; h->header_bytes = sizeof *h;
  h->boot_id = log_ticks(); h->first_lba = first; h->window_blocks = (uint32_t)blocks;
  h->msg_first = LogMsgFirst; h->msg_blocks = LogMsgBlocks; h->slot_first = LogSlotFirst; h->slot_blocks = LogSlotBlocks;
  h->slot_count = (uint32_t)((blocks - LogSlotFirst) / LogSlotBlocks);
  h->gpt_open = h->gpt_last = &native_ufs_store_gpt_check ? native_ufs_store_gpt_check() : 0xff;
  strlcpy(h->tag, "blueline", sizeof h->tag);
  uint64_t f = 0; __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(f)); if (!f) f = 19200000;
  boot_log_freq = f;
  boot_log_start_ticks = log_ticks();
  IOLog("BluelineLog: writing to LBA 0x%llx, %llu blocks\n", (unsigned long long)first, (unsigned long long)blocks);
  boot_log_state = 2;
  for (;;) {
    IOLockLock(boot_log_lock);
    if (boot_log_state == 2) round_locked("periodic");
    const uint32_t state = boot_log_state;
    IOLockUnlock(boot_log_lock);
    if (state != 2) return;
    IOSleep(LogPeriodMs);
  }
}

// Called once from the display driver's early probe; starts the writer when sheng-log=1.
void blueline_log_start(void) {
  static int started;
  if (started) return;
  started = 1;
  uint32_t mode = 0;
  if (!PE_parse_boot_argn("sheng-log", &mode, sizeof mode) || mode != 1) return;
  if (!&native_ufs_log_open || !&native_ufs_log_write || !&native_ufs_log_sync) return;
  boot_log_buffer = (uint8_t *)IOMallocAligned(LogMsgBlocks * LogBlock, LogBlock);
  boot_log_lock = IOLockAlloc();
  if (!boot_log_buffer || !boot_log_lock) return;
  // Logging must not inject or acknowledge interrupts. The old startup probe could access EL3-owned Group 0 before the
  // writer existed, losing both the boot and its evidence. Observe ArmCpu's passive [cpu] counters instead.
  thread_t thread;
  if (kernel_thread_start(writer, 0, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}
