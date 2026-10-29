// SPDX-License-Identifier: BSD-2-Clause-Patent
// Blueline SMP start-up check and per-core diagnostics. Built with XNU's osfmk recipe (cpu_common.o) so it can read every
// core's cpu_data. Ported from:
// - ios-sweet SweetCPU::startAll (refs/ios-flame 0065-sweet-smp-psci.patch): after a core starts, wait up to 5 s until its
//   self-SGI has cleared SIGPdisabled (sweet_cpu_signal_is_enabled), "came alive; SGI1 OK".
// - pixel3-ios 0116-blueline-smp-diag.patch: per core the cpu_signal flags, the scheduler's state and priority, and the GICv3
//   redistributor's SGI pending/enable bits and WAKER.
// Why: a core whose start-up self-IPI never lands keeps SIGPdisabled, so cpu_signal() fails for it and DebuggerXCallEnter skips
// it without a word; it then runs through stackshots (blueline run 50: assert(not_in_kdp) next to the count mismatch).
// Not in the references: ios-sweet panics on a timeout. Here the core is recorded and then sent IPIs from the checking core
// through the kernel's own PE_cpu_signal path (blueline-smp-kick=0 turns that off), so the log tells a lost self-IPI from a core
// no IPI reaches. Only GIC reads: logging must not touch EL3-owned Group 0 (the run 47 probe lost the boot).
#include <kern/processor.h>
#include <kern/thread.h>
#include <arm/cpu_data_internal.h>
#include <arm/machine_routines.h>
#include <pexpert/pexpert.h>

extern void IOSleep(unsigned int milliseconds);
void blueline_smp_sample(void);
void blueline_smp_start(void);

#define SMP_CORES     8
#define SMP_GICR_PA   0x17a60000u   // SDM845 redistributors (stock DT gic@17a00000, second reg), one 0x20000 frame per core
#define SMP_GICR_SIZE 0x20000u
#define SMP_STARTED   (InitState | StartedState)

volatile uint32_t blueline_smp_cores;
volatile uint32_t blueline_smp_sig[SMP_CORES];                 // cpu_signal flags (SIGPdisabled = 0x80000000)
volatile int32_t blueline_smp_state[SMP_CORES], blueline_smp_pri[SMP_CORES];
volatile uint32_t blueline_smp_gicr[SMP_CORES][3];             // GICR_ISPENDR0, GICR_ISENABLER0 (SGI frame), GICR_WAKER
// Start-up check per core: -1 not checked yet, -3 never started (no cpu_data or not Init+Started within 5 s), 0..5000 ms until
// SIGPdisabled cleared on its own (the self-IPI landed), 10000 + n cleared after n IPIs from the checking core, -2 never cleared.
volatile int32_t blueline_smp_check[SMP_CORES] = {-1, -1, -1, -1, -1, -1, -1, -1};

static vm_offset_t smp_gicr;

static vm_offset_t smp_gicr_frame(uint32_t phys_id)
{
  for (uint32_t f = 0; smp_gicr && f < SMP_CORES; f++) {
    vm_offset_t b = smp_gicr + f * SMP_GICR_SIZE;
    uint64_t typer = *(volatile uint64_t *)(b + 0x8);
    if (((typer >> 32) & 0xffff) == (phys_id & 0xffff)) {
      return b;
    }
  }
  return 0;
}

static bool smp_signal_enabled(cpu_data_t *cdp)
{
  return cdp != NULL && !(os_atomic_load(&cdp->cpu_signal, acquire) & SIGPdisabled);
}

static unsigned smp_count(const ml_topology_info_t *topology)
{
  return topology->num_cpus < SMP_CORES ? topology->num_cpus : SMP_CORES;
}

void blueline_smp_sample(void)
{
  const ml_topology_info_t *topology = ml_get_topology_info();
  const unsigned n = smp_count(topology);
  blueline_smp_cores = n;
  if (!smp_gicr) {
    smp_gicr = ml_io_map(SMP_GICR_PA, SMP_CORES * SMP_GICR_SIZE);
  }
  for (unsigned i = 0; i < n; i++) {
    cpu_data_t *cdp = CpuDataEntries[i].cpu_data_vaddr;
    processor_t p = processor_array[i];
    vm_offset_t r = smp_gicr_frame(topology->cpus[i].phys_id);
    blueline_smp_sig[i] = cdp ? os_atomic_load(&cdp->cpu_signal, relaxed) : 0xffffffffu;
    blueline_smp_state[i] = p ? (int32_t)p->state : -1;
    blueline_smp_pri[i] = p ? p->current_pri : -1;
    blueline_smp_gicr[i][0] = r ? *(volatile uint32_t *)(r + 0x10200) : 0xdeadu;   // GICR_ISPENDR0
    blueline_smp_gicr[i][1] = r ? *(volatile uint32_t *)(r + 0x10100) : 0xdeadu;   // GICR_ISENABLER0
    blueline_smp_gicr[i][2] = r ? *(volatile uint32_t *)(r + 0x14) : 0xdeadu;      // GICR_WAKER
  }
}

static void smp_check(void *arg, wait_result_t result)
{
  (void)arg; (void)result;
  thread_set_thread_name(current_thread(), "blueline_smp");
  unsigned kick = 1;
  PE_parse_boot_argn("blueline-smp-kick", &kick, sizeof kick);
  const ml_topology_info_t *topology = ml_get_topology_info();
  const unsigned n = smp_count(topology);
  for (unsigned i = 0; i < n; i++) {
    cpu_data_t *cdp = NULL;
    unsigned ms = 0;
    for (;;) {
      cdp = CpuDataEntries[i].cpu_data_vaddr;
      if (cdp != NULL && (os_atomic_load(&cdp->cpu_flags, relaxed) & SMP_STARTED) == SMP_STARTED) {
        break;
      }
      if (ms >= 5000) {
        cdp = NULL;
        break;
      }
      IOSleep(10);
      ms += 10;
    }
    if (cdp == NULL) {
      blueline_smp_check[i] = -3;
      continue;
    }
    for (ms = 0; !smp_signal_enabled(cdp) && ms < 5000; ms += 10) {
      IOSleep(10);
    }
    if (smp_signal_enabled(cdp)) {
      blueline_smp_check[i] = (int32_t)ms;
      continue;
    }
    blueline_smp_check[i] = -2;
    for (unsigned k = 1; kick && k <= 20; k++) {
      boolean_t enabled = ml_set_interrupts_enabled(FALSE);
      cpu_data_t *self = getCpuDatap();
      if (self != cdp) {
        PE_cpu_signal(self->cpu_id, cdp->cpu_id);
      }
      ml_set_interrupts_enabled(enabled);
      IOSleep(100);
      if (smp_signal_enabled(cdp)) {
        blueline_smp_check[i] = 10000 + (int32_t)k;
        break;
      }
    }
  }
  blueline_smp_sample();
  printf("BluelineSmp: check");
  for (unsigned i = 0; i < n; i++) {
    printf(" %d", blueline_smp_check[i]);
  }
  printf(" | sig");
  for (unsigned i = 0; i < n; i++) {
    printf(" %x", blueline_smp_sig[i]);
  }
  printf("\n");
}

// Called once from blueline_log_start (the display driver's early probe), whether or not the UFS log is on.
void blueline_smp_start(void)
{
  static int started;
  if (started) {
    return;
  }
  started = 1;
  thread_t thread;
  if (kernel_thread_start(smp_check, NULL, &thread) == KERN_SUCCESS) {
    thread_deallocate(thread);
  }
}
