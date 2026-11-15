/* SPDX-License-Identifier: MIT */
/* Copyright (c) 2026 NativeAdreno contributors. See ../upstream/Adreno6xx/LICENSE. */
/* G1 mechanism port from the supplied nabu 16.5 source snapshot:
 * patches/nabu-gpu/NabuGpuClientSlots.h (sha256 c46b567759f6746f8ce70fc9d53a0b1b2d8825b269e146dfbcffebb6e259a036)
 * patches/nabu-gpu/NabuGpuAddressSpace.h (sha256 f577c0471591267b0bd83b09059c5799cf8037529ea2b565642c7ad5b6046a32).
 * Local change: one default-off build gate selects the client/window/table
 * contract together. No Darwin 22 addresses, object layouts or vtables used.
 * The GMU retains its independent existing 2 GiB aperture and table budget. */
#ifndef NABU_GPU_CLIENT_POLICY_H
#define NABU_GPU_CLIENT_POLICY_H
#include <stddef.h>
#include <stdint.h>
#ifndef NABU_GPU_CLIENTS_24
#define NABU_GPU_CLIENTS_24 0
#endif
#if NABU_GPU_CLIENTS_24 != 0 && NABU_GPU_CLIENTS_24 != 1
#error NABU_GPU_CLIENTS_24_must_be_zero_or_one
#endif
#if NABU_GPU_CLIENTS_24 && (!defined(NABU_GPU_METAL) || NABU_GPU_METAL != 1)
#error NABU_GPU_CLIENTS_24_requires_Metal
#endif
enum : unsigned {NabuGpuMaxClients=NABU_GPU_CLIENTS_24?24:4};
enum : uint64_t {NabuGpuApertureBytes=UINT64_C(1)<<(NABU_GPU_CLIENTS_24?32:31)};
enum : size_t {
    NabuGpuApertureLeaves=NabuGpuApertureBytes/4096,
    NabuGpuTablePages=1+NabuGpuApertureBytes/(UINT64_C(1)<<30)
                       +NabuGpuApertureBytes/(UINT64_C(1)<<21)
};
/* Embedded in each policy consumer for object/final-image qualification.
 * These are build constants, not a user-client ABI or a runtime success flag. */
struct NabuGpuClientPolicyRecord {
    uint64_t version,enabled,maxClients,apertureBytes,tablePages,leafBudget;
};
static constexpr NabuGpuClientPolicyRecord NabuGpuCompiledClientPolicy={
    1,NABU_GPU_CLIENTS_24,NabuGpuMaxClients,NabuGpuApertureBytes,
    NabuGpuTablePages,NabuGpuApertureLeaves
};
static_assert(sizeof(NabuGpuClientPolicyRecord)==48,"GPU policy record layout");
struct NabuGpuClientWindow {uint64_t start,bytes;};
static constexpr NabuGpuClientWindow NabuGpuClientWindows[NabuGpuMaxClients]={
    {0x20000000ull,0x40000000ull},{0x60000000ull,0x0AAA9000ull},
    {0x6AAA9000ull,0x0AAA9000ull},{0x75552000ull,0x0AAA9000ull},
#if NABU_GPU_CLIENTS_24
    {0x00800000ull,0x07E00000ull},{0x08600000ull,0x07E00000ull},
    {0x10400000ull,0x07E00000ull},{0x18200000ull,0x07E00000ull},
    {0x80000000ull,0x08000000ull},{0x88000000ull,0x08000000ull},
    {0x90000000ull,0x08000000ull},{0x98000000ull,0x08000000ull},
    {0xA0000000ull,0x08000000ull},{0xA8000000ull,0x08000000ull},
    {0xB0000000ull,0x08000000ull},{0xB8000000ull,0x08000000ull},
    {0xC0000000ull,0x08000000ull},{0xC8000000ull,0x08000000ull},
    {0xD0000000ull,0x08000000ull},{0xD8000000ull,0x08000000ull},
    {0xE0000000ull,0x08000000ull},{0xE8000000ull,0x08000000ull},
    {0xF0000000ull,0x08000000ull},{0xF8000000ull,0x08000000ull},
#endif
};
inline unsigned NabuGpuSelectClientSlot(bool compositor,
    const bool (&occupied)[NabuGpuMaxClients])
{
    if(compositor && !occupied[0])return 0;
    for(unsigned slot=1;slot<NabuGpuMaxClients;++slot)if(!occupied[slot])return slot;
    return NabuGpuMaxClients;
}
constexpr bool NabuGpuClientWindowsValid()
{
    for(unsigned i=0;i<NabuGpuMaxClients;++i) {
        const auto a=NabuGpuClientWindows[i];
        // Below 8 MiB is reserved for GMEM and the existing bootstrap mappings.
        if(a.start<0x800000 || !a.bytes || ((a.start|a.bytes)&4095) ||
            a.start>=NabuGpuApertureBytes || a.bytes>NabuGpuApertureBytes-a.start)return false;
        for(unsigned j=0;j<i;++j) {
            const auto b=NabuGpuClientWindows[j];
            if(a.start<b.start+b.bytes && b.start<a.start+a.bytes)return false;
        }
    }
    return true;
}
static_assert(NabuGpuClientWindowsValid(),"GPU client windows must be disjoint and inside the aperture");
#endif
