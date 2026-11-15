/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/* Copyright (c) 2026, UniXNU contributors. All rights reserved.
 * Mechanism adapted from supplied patches/nabu-power/NabuCpuFreq.h
 * SHA256 5a6e288c8c36326867c10862993827a39e102e47ec0b0c0fd9a2ff82db24c771.
 * The BSD-2-Clause-Patent text is in nabu-device/LICENSE.
 * No donor kernel ABI is used. The high bound is an observed firmware row,
 * not a copied donor maximum frequency. */
#ifndef NABU_CPU_FREQ_H
#define NABU_CPU_FREQ_H
#include <stdint.h>
#include <stddef.h>
namespace NabuCpuFreqPolicy {
enum {Domains=4,MaxCpus=8,Rows=40};
struct Table {uint32_t khz[Rows]{};unsigned rows=0,low=0,warm=0,high=0;};
inline bool decode(const uint32_t *words,unsigned initial,Table &out) {
    if(!words)return false;
    Table t;unsigned cores[Rows]{},most=0;
    for(unsigned i=0;i<Rows;++i) {
        uint32_t hz=words[i]>>30?19200u*(words[i]&255):300000u;
        if(!hz)return false;
        if(i&&hz==t.khz[i-1])break;
        if(i&&hz<t.khz[i-1])return false;
        t.khz[i]=hz;cores[i]=(words[i]>>16)&7;t.rows=i+1;
        if(cores[i]>most)most=cores[i];
    }
    if(!most||t.rows<2||initial>=t.rows||cores[initial]!=most)return false;
    t.high=initial;t.low=initial;t.warm=initial;
    bool found=false,warm=false;
    for(unsigned i=0;i<=initial;++i)if(cores[i]==most) {
        if(!found){t.low=i;found=true;}
        if(uint64_t(t.khz[i])*3<=uint64_t(t.khz[initial])*2){t.warm=i;warm=true;}
    }
    if(!found)return false;
    if(!warm)t.warm=t.low;
    out=t;return true;
}
inline bool temperature(const uint32_t *values,unsigned count,int32_t &hottest) {
    if(!values||!count)return false;
    bool valid=false;int32_t high=-2730;
    for(unsigned i=0;i<count;++i)if(values[i]&(1u<<21)) {
        const uint32_t v=values[i]&4095;
        const int32_t t=v&2048?int32_t(v)-4096:int32_t(v);
        if(t < -400 || t > 1250)continue;
        if(!valid||t>high)high=t;
        valid=true;
    }
    if(valid)hottest=high;
    return valid;
}
inline unsigned thermal(unsigned prior,int32_t t) {
    if(t>=1050)return 2;
    if(prior==2)return t>=950?2:1;
    if(t>=950)return 1;
    return prior==1&&t>=850?1:0;
}
struct Demand {bool high=false;unsigned quiet=0;};
inline void demand(Demand &d,unsigned percent) {
    if(percent>=25){d.high=true;d.quiet=0;}
    else if(percent>=10)d.quiet=0;
    else if(d.high&&++d.quiet>=10){d.high=false;d.quiet=0;}
}
inline bool busy(uint64_t elapsed,uint64_t before,uint64_t after,unsigned &out) {
    if(!elapsed||after<before||after-before>elapsed)return false;
    const uint64_t active=elapsed-(after-before);
    // The worker's bounded 100 ms interval prevents multiplication overflow.
    if(active>UINT64_MAX/100)return false;
    out=unsigned(active*100/elapsed);return true;
}
inline unsigned row(const Table &t,bool high,unsigned cap) {
    return !high||cap==2?t.low:cap==1?t.warm:t.high;
}
}
extern "C" void nabu_cpufreq_start(void);
#endif
