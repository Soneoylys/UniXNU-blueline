/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#ifndef BLUELINE_GPU_POLICY_H
#define BLUELINE_GPU_POLICY_H
#include "AdrenoHfiStartup.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    uint32_t chip_id;
    const uint8_t *gfx,*mx,*cx;
    size_t gfx_bytes,mx_bytes,cx_bytes;
} BluelineGpuArcInputs;
/* Board OPPs: stock Pixel 3 frequency list, Linux v6.12 SDM845 rail levels.
 * Only the common 200 MHz GMU level is enabled (stock 500 MHz differs from
 * mainline's 400 MHz). Rail aux bytes must be retained, immutable snapshots
 * from the actual command database, not synthetic fallback tables.
 * Inputs/output must be disjoint. Failure leaves output unchanged; success
 * is arithmetic only, and does not establish power/clock readiness. */
AdrenoHfiStartupStatus BluelineGpuBuildStartup(const BluelineGpuArcInputs *,AdrenoHfiStartupConfig *);
#ifdef __cplusplus
}
#endif
#endif
