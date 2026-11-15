/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/* Copyright (c) 2026, UniXNU contributors. All rights reserved.
 * Ported mechanism: supplied patches/nabu-power/NabuCpuFreq.cpp
 * SHA256 341ab9492ac04afc536325367cb80c42327ad8a8586bef67658cd584f88e0369
 * and NabuCpuFreq.h (source digest and license pointer in local header);
 * 10063 native recount helper replaces the donor ml_cpu_idle_time ABI.
 * Stock maximum constants are intentionally replaced by firmware-row caps. */
#ifndef XNU_NABU_CPUFREQ
#define XNU_NABU_CPUFREQ 0
#endif
#if XNU_NABU_CPUFREQ != 0 && XNU_NABU_CPUFREQ != 1
#error XNU_NABU_CPUFREQ_must_be_zero_or_one
#endif
#include "NabuCpuFreq.h"
#ifndef NABU_CPUFREQ_SOURCE_ID
#if XNU_NABU_CPUFREQ
#error Enabled_cpufreq_requires_bound_source_identity
#else
#define NABU_CPUFREQ_SOURCE_ID "unbound-default-off"
#endif
#endif
extern "C" {
extern __attribute__((used)) const char nabu_cpufreq_identity[]="NABUCPUFREQ_SOURCE_SHA256=" NABU_CPUFREQ_SOURCE_ID;
extern __attribute__((used)) const uint32_t nabu_cpufreq_build_policy[4]={1,XNU_NABU_CPUFREQ,100,8};
}
#if XNU_NABU_CPUFREQ
#include <IOKit/IOLib.h>
#include <kern/clock.h>
#include <kern/thread.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include "NabuPerfConfig.h"
extern "C" {
vm_offset_t ml_io_map(vm_offset_t,vm_size_t);
int nabu_cpufreq_sample_idle(uint64_t *,unsigned,unsigned *);
kern_return_t thread_terminate(thread_t);
}
using namespace NabuCpuFreqPolicy;
static unsigned started;
static const uint64_t physical[Domains]={0x18321000,0x18323000,0x18325800,0x18327800};
static const unsigned cores[Domains]={1,4,3,1};
static bool conflict() {
    const auto c=nabuPerfParse(PE_boot_args());
    if(c.malformed||c.l3TargetMHz)return true;
    for(auto value:c.cpuTargetMHz)if(value)return true;
    for(unsigned n=0;n<c.stepCount;++n) {
        if(c.steps[n].l3MHz)return true;
        for(auto value:c.steps[n].cpuMHz)if(value)return true;
    }
    return false;
}
static bool readTemperature(volatile uint32_t *regs,int32_t &value) {
    uint32_t status[14];
    for(unsigned n=0;n<14;++n)status[n]=regs[(0x3000+0xa0+4*(n+1))/4];
    return temperature(status,14,value);
}
__attribute__((noinline)) static void run() {
    const auto topology=ml_get_topology_info();
    if(!topology||!topology->cpus||!topology->num_cpus||topology->num_cpus>MaxCpus)return;
    unsigned domain[MaxCpus];bool seen[MaxCpus]{};
    for(unsigned n=0;n<topology->num_cpus;++n) {
        const auto &cpu=topology->cpus[n];
        const unsigned core=(cpu.phys_id>>8)&255;
        if(cpu.cpu_id!=n||(cpu.phys_id&~uint32_t(0x700))||core>=MaxCpus||seen[core])return;
        seen[core]=true;domain[n]=core<4?1:core<7?2:3;
    }
    uint64_t previous[MaxCpus]{};unsigned count=0;
    if(nabu_cpufreq_sample_idle(previous,MaxCpus,&count)||count!=topology->num_cpus)return;
    const auto osm=ml_io_map(0x18320000,0xc000);
    const auto rawTsens=ml_io_map(0x0c260000,0x4000);
    if(!osm||!rawTsens){IOLog("NABU CPUFREQ: map failure; no votes\n");return;}
    auto tsens=reinterpret_cast<volatile uint32_t *>(rawTsens);
    volatile uint32_t *regs[Domains];Table tables[Domains];unsigned requested[Domains],requests[Domains];
    for(unsigned d=0;d<Domains;++d) {
        regs[d]=reinterpret_cast<volatile uint32_t *>(osm+(physical[d]-0x18320000));
        uint32_t words[Rows];for(unsigned n=0;n<Rows;++n)words[n]=regs[d][(0x110+n*32)/4];
        requested[d]=regs[d][0x920/4];
        if(!(regs[d][0]&1)||!decode(words,requested[d],tables[d])) {
            IOLog("NABU CPUFREQ: domain=%u inactive/invalid firmware LUT; no votes\n",d);return;
        }
        requests[d]=regs[d][0xbc/4]&1?cores[d]:1;
    }
    int32_t hottest=0;
    if(!readTemperature(tsens,hottest)){IOLog("NABU CPUFREQ: no valid TSENS; no votes\n");return;}
    auto apply=[&](unsigned d,unsigned want) {
        bool same=true;
        for(unsigned n=0;n<requests[d];++n)if(regs[d][0x920/4+n]!=want)same=false;
        if(same){requested[d]=want;return true;}
        for(unsigned n=0;n<requests[d];++n)regs[d][0x920/4+n]=want;
        __asm__ volatile("dsb sy":::"memory");
        for(unsigned n=0;n<requests[d];++n)if(regs[d][0x920/4+n]!=want)return false;
        requested[d]=want;
        return true;
    };
    Demand small,fast;unsigned cap=0;uint64_t then=mach_absolute_time();
    if(nabu_cpufreq_sample_idle(previous,MaxCpus,&count)||count!=topology->num_cpus)return;
    IOLog("NABU CPUFREQ: governor enabled count=%u sample=100ms; upper rows observed at boot\n",count);
    for(unsigned sample=0;;++sample) {
        IOSleep(100);
        unsigned peaks[Domains]{};uint64_t values[MaxCpus]{};unsigned nowCount=0;
        const uint64_t beginning=mach_absolute_time();
        bool valid=!nabu_cpufreq_sample_idle(values,MaxCpus,&nowCount)&&nowCount==count;
        const uint64_t now=mach_absolute_time();
        if(beginning<=then||now<beginning)valid=false;
        for(unsigned n=0;valid&&n<count;++n) {
            unsigned percent=0;valid=busy(now-then,previous[n],values[n],percent);
            if(percent>peaks[domain[n]])peaks[domain[n]]=percent;
        }
        valid=valid&&readTemperature(tsens,hottest);
        if(!valid) {
            for(unsigned d=0;d<Domains;++d)if(!apply(d,tables[d].low))
                IOLog("NABU CPUFREQ: low vote readback failed domain=%u\n",d);
            IOLog("NABU CPUFREQ: invalid load/temperature; low votes then stopped\n");return;
        }
        for(unsigned n=0;n<count;++n)previous[n]=values[n];
        // Idle snapshots occur inside these clock brackets. Use the enclosing
        // interval so an all-idle CPU is not rejected for sampler overhead.
        then=beginning;
        demand(small,peaks[1]);demand(fast,peaks[2]>peaks[3]?peaks[2]:peaks[3]);cap=thermal(cap,hottest);
        const bool high[Domains]={small.high||fast.high,small.high,fast.high,fast.high};
        for(unsigned d=0;d<Domains;++d)if(!apply(d,row(tables[d],high[d],cap))) {
            IOLog("NABU CPUFREQ: vote readback failed domain=%u; stopped\n",d);return;
        }
        if(sample%50==0)IOLog("NABU CPUFREQ: rows=%u/%u/%u/%u cap=%u hottest=%d tenths\n",
            requested[0],requested[1],requested[2],requested[3],cap,hottest);
    }
}
static void worker(void *,wait_result_t) {run();thread_terminate(current_thread());}
extern "C" void nabu_cpufreq_start(void) {
    unsigned mode=0;
    if(!PE_parse_boot_argn("nabu-cpufreq",&mode,sizeof(mode))||mode!=1)return;
    if(conflict()){IOLog("NABU CPUFREQ: conflicting CPU/L3 performance owner; refused\n");return;}
    unsigned expected=0;if(!__atomic_compare_exchange_n(&started,&expected,1,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))return;
    thread_t thread=nullptr;
    const auto status=kernel_thread_start(worker,nullptr,&thread);
    if(status!=KERN_SUCCESS){IOLog("NABU CPUFREQ: worker creation failed %d; no votes\n",status);return;}
    thread_deallocate(thread);
}
#else
extern "C" void nabu_cpufreq_start(void) {}
#endif
