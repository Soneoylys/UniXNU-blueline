/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/* Synthetic command-db rails; production policy never supplies fallback rails. */
#include "BluelineGpuPolicy.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(x) do {++checks;if(!(x)){fprintf(stderr,"policy %d: %s\n",__LINE__,#x);exit(1);}}while(0)
static unsigned checks;
/* No transport is allowed during board startup arithmetic. */
AdrenoHfiStatus AdrenoHfiSend(AdrenoHfi *a,unsigned b,const uint32_t *c,size_t d,uint32_t *e)
{(void)a;(void)b;(void)c;(void)d;(void)e;abort();}
AdrenoHfiStatus AdrenoHfiPoll(AdrenoHfi *a,uint32_t *b,size_t c,size_t *d,uint32_t *e)
{(void)a;(void)b;(void)c;(void)d;(void)e;abort();}
int main(void)
{
    /* GFX/CX and MX have different index orderings and secondary floors. */
    static const uint8_t gfx[]={0,0,64,0,128,0,192,0,0,1,64,1,128,1,160,1};
    static const uint8_t mx[]={0,0,48,0,64,0,128,0,192,0,0,1,64,1,128,1,160,1};
    static const uint8_t cx[]={0,0,48,0,64,0,128,0};
    BluelineGpuArcInputs in={0x06030001,gfx,mx,cx,sizeof gfx,sizeof mx,sizeof cx};
    AdrenoHfiStartupConfig c;memset(&c,0xaa,sizeof c);
    CHECK(BluelineGpuBuildStartup(&in,&c)==AdrenoHfiStartupSuccess);
    CHECK(c.gpu_count==8 && c.gmu_count==2 && c.gpu[0].hz==0 && c.gpu[0].vote==0);
    CHECK(c.gpu[1].hz==257000000 && c.gpu[1].vote==0x00400201);
    CHECK(c.gpu[7].hz==710000000 && c.gpu[7].vote==0x01a00807);
    CHECK(c.gmu[1].hz==200000000 && c.gmu[1].vote==0x00300101 && c.gmu[2].hz==0);
    CHECK(c.bandwidth_count==1 && c.ddr_count==3 && c.ddr_wait_mask==7 && c.cnoc_count==3 && c.cnoc_wait_mask==5);
    CHECK(c.ddr_addresses[1]==0x5005c && c.cnoc_addresses[0]==0x50034 && c.cnoc_addresses[1]==0x5007c && c.cnoc_addresses[2]==0x5004c);
    CHECK(c.cnoc_data[0][1]==0 && c.cnoc_data[1][1]==0x20000001 && c.ddr_data[0][2]==0x40000000);
    CHECK(!c.enable_ifpc && !c.acd_enable_mask && AdrenoHfiStartupValidate(&c)==0);
    AdrenoHfiLegacyBoot boot={0x60400000,0x4000,1};uint32_t id,words[159];size_t n;
    CHECK(AdrenoHfiStartupEncodeLegacy(&c,&boot,AdrenoHfiStagePerformance,&id,words,159,&n)==0 && id==4 && n==42);
    CHECK(words[4]==0x00400201 && words[5]==257000 && words[36]==0x00300101 && words[37]==200000);
    CHECK(AdrenoHfiStartupEncodeLegacy(&c,&boot,AdrenoHfiStageBandwidth,&id,words,159,&n)==0 && id==3 && n==159);
    const AdrenoHfiStartupConfig before=c;
    for(unsigned fault=0;fault<7;++fault) {
        BluelineGpuArcInputs bad=in;
        switch(fault) {
        case 0:bad.chip_id=0x06040001;break;
        case 1:bad.gfx=NULL;break;
        case 2:--bad.mx_bytes;break;
        case 3:bad.gfx_bytes-=2;break; /* actual primary rail lacks TURBO_L1 */
        case 4:bad.cx_bytes=2;break;
        case 5:bad.gfx_bytes=0;break;
        case 6:bad.mx_bytes=0;break;
        }
        CHECK(BluelineGpuBuildStartup(&bad,&c)==AdrenoHfiStartupInvalid && !memcmp(&c,&before,sizeof c));
    }
    CHECK(BluelineGpuBuildStartup(NULL,&c)==AdrenoHfiStartupInvalid && !memcmp(&c,&before,sizeof c));
    CHECK(BluelineGpuBuildStartup(&in,NULL)==AdrenoHfiStartupInvalid);
    printf("PASS: %u board GPU policy checks, synthetic rails, no transport/hardware\n",checks);
}
