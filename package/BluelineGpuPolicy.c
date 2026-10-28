/* SPDX-License-Identifier: BSD-2-Clause-Patent */
#include "BluelineGpuPolicy.h"
#include "AdrenoHfiVotes.h"
#include "Sdm845GpuHfi.h"
AdrenoHfiStartupStatus BluelineGpuBuildStartup(const BluelineGpuArcInputs *in,AdrenoHfiStartupConfig *out)
{
    if(!in || !out || in->chip_id!=0x06030001)return AdrenoHfiStartupInvalid;
    /* Pixel SP1A.210812.016.C2 DTB0 frequencies agree with Linux v6.12 SDM845.
     * Numeric RPMh levels: LOW_SVS, SVS, SVS_L1, NOM, NOM_L1, TURBO, TURBO_L1.
     * No custom PLL/voltage values are derived from these frequencies. */
    static const uint32_t gpu_hz[]={0,257000000,342000000,414000000,520000000,596000000,675000000,710000000};
    static const uint32_t gpu_level[]={0,64,128,192,256,320,384,416};
    static const uint32_t gmu_level[]={0,48}; /* MIN_SVS at 200 MHz */
    uint32_t gpu_votes[8],gmu_votes[2];
    if(AdrenoHfiArcVotes(gpu_level,8,in->gfx,in->gfx_bytes,in->mx,in->mx_bytes,gpu_votes,8)!=AdrenoHfiStartupSuccess ||
        AdrenoHfiArcVotes(gmu_level,2,in->cx,in->cx_bytes,in->mx,in->mx_bytes,gmu_votes,2)!=AdrenoHfiStartupSuccess)
        return AdrenoHfiStartupInvalid;
    AdrenoHfiStartupConfig c={0};c.gpu_count=8;c.gmu_count=2;
    for(unsigned i=0;i<8;++i)c.gpu[i]=(AdrenoHfiPerformanceLevel){gpu_hz[i],gpu_votes[i]};
    c.gmu[1]=(AdrenoHfiPerformanceLevel){200000000,gmu_votes[1]};
    if(Sdm845GpuHfiBandwidth(&c)!=AdrenoHfiStartupSuccess || AdrenoHfiStartupValidate(&c)!=AdrenoHfiStartupSuccess)
        return AdrenoHfiStartupInvalid;
    *out=c;return AdrenoHfiStartupSuccess;
}
