// SPDX-License-Identifier: BSD-2-Clause-Patent
// Default-off KTZ8866 probe with a separately compiled Legacy PM hook.
// Ported from donor GPU242 cb45dcf71cd7381c1d39966e411c3ed69a512fbf,
// patches/nabu-display/NabuBacklight.cpp. The local FDT binding, uncached
// mapping and bias-disable GPIO controls remain. Transactions now own their
// Pins and mapping; a single atomically published mutex serializes probe and
// power, including requalification. The published mutex lives until reboot.
#include "NabuBacklight.h"

#if XNU_NABU_BACKLIGHT
#include <IOKit/IOLib.h>
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <IOKit/IOLocks.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IORegistryEntry.h>
#include <libkern/c++/OSData.h>
#include <pexpert/pexpert.h>
#include <machine/machine_routines.h>
#include <string.h>

namespace {
uint32_t started = 0;
// Access only while holding powerLock. A failed re-probe revokes old readiness.
bool ready = false;
IOLock *powerLock = nullptr;

IOLock *transactionLock()
{
    IOLock *lock = __atomic_load_n(&powerLock, __ATOMIC_ACQUIRE);
    if (lock) return lock;
    IOLock *allocated = IOLockAlloc();
    if (!allocated) return nullptr;
    IOLock *expected = nullptr;
    if (__atomic_compare_exchange_n(&powerLock, &expected, allocated, false,
                                     __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return allocated;
    IOLockFree(allocated);
    return expected;
}
struct Locked {
    IOLock *lock;
    explicit Locked(IOLock *value) : lock(value) { IOLockLock(lock); }
    ~Locked() { IOLockUnlock(lock); }
};
struct Pins {
    volatile uint32_t *base = nullptr;
    uint32_t config[2], output[2];
    uint32_t read(unsigned pin, unsigned reg) const {
        return base[(pin * 0x1000 + reg) / 4];
    }
    void write(unsigned pin, unsigned reg, uint32_t value) {
        base[(pin * 0x1000 + reg) / 4] = value;
#if defined(__aarch64__)
        __asm__ volatile("dsb sy" ::: "memory");
#else
        __asm__ volatile("" ::: "memory");
#endif
    }
    void take() {
        for (unsigned i = 0; i < 2; ++i) {
            config[i] = read(i, 0);
            output[i] = read(i, 4) & nabu_backlight::IoOutBit;
        }
    }
    void restore() {
        // Restore OUT before mux/config, without latching the read-only IN bit.
        for (unsigned i = 0; i < 2; ++i) write(i, 4, output[i]);
        for (unsigned i = 0; i < 2; ++i) write(i, 0, config[i]);
    }
    void line(bool scl, bool high) {
        unsigned pin = scl ? nabu_backlight::SclPin : nabu_backlight::SdaPin;
        write(pin, 4, 0);
        write(pin, 0, high ? nabu_backlight::CtlRelease : nabu_backlight::CtlDriveLow);
    }
    bool level(bool scl) const {
        return (read(scl ? nabu_backlight::SclPin : nabu_backlight::SdaPin, 4)
                & nabu_backlight::IoInBit) != 0;
    }
    void delay_us(unsigned us) { IODelay(us); }
};
struct Transaction {
    IOMemoryMap *mapping = nullptr;
    Pins pins;
    Transaction() {
        IODeviceMemory *memory = IODeviceMemory::withRange(nabu_backlight::GpioTileBase,
                                                         nabu_backlight::MapBytes);
        if (memory) {
            mapping = memory->map(kIOMapAnywhere | kIOMapInhibitCache);
            memory->release();
        }
        if (mapping && mapping->getVirtualAddress() &&
            mapping->getLength() == nabu_backlight::MapBytes)
            pins.base = reinterpret_cast<volatile uint32_t *>(mapping->getVirtualAddress());
    }
    ~Transaction() { if (mapping) mapping->release(); }
};
bool threadContext() { return !ml_at_interrupt_context() && ml_get_interrupts_enabled(); }
bool bootArgEnabled(const char *key)
{
    uint32_t enabled = 0;
    return PE_parse_boot_argn(key, &enabled, sizeof(enabled)) && enabled == 1;
}
bool boundFdt(IORegistryEntry *chosen)
{
    const OSData *digest = OSDynamicCast(OSData, chosen->getProperty(nabu_backlight::FdtProperty));
    return digest && digest->getLength() == sizeof(nabu_backlight::SourceFdtSha256) &&
        digest->getBytesNoCopy() &&
        memcmp(digest->getBytesNoCopy(), nabu_backlight::SourceFdtSha256,
               sizeof(nabu_backlight::SourceFdtSha256)) == 0;
}
void runProbeLocked()
{
    ready = false;
    IORegistryEntry *chosen = IORegistryEntry::fromPath("/chosen", gIODTPlane);
    if (!chosen || !boundFdt(chosen)) {
        IOLog("NABU BACKLIGHT gate=source-fdt-binding\n");
        if (chosen) chosen->release();
        return;
    }
    chosen->release();
    Transaction transaction;
    if (!transaction.pins.base) {
        IOLog("NABU BACKLIGHT gate=gpio-map\n");
        return;
    }
    const nabu_backlight::Probe result = nabu_backlight::probe(transaction.pins);
    ready = result.idle;
    IOLog("NABU BACKLIGHT probe %s reg08=%#x bus-error=%u power-hook-compiled=%u runtime-qualified=0\n",
          ready ? "ok" : "failed", result.reg08, unsigned(result.error), XNU_NABU_BACKLIGHT_POWER);
}
} // namespace
#endif

extern "C" bool nabu_backlight_start(void)
{
#if XNU_NABU_BACKLIGHT
    if (!bootArgEnabled("nabu-backlight") || !threadContext()) return false;
    uint32_t expected = 0;
    if (!__atomic_compare_exchange_n(&started, &expected, 1, false,
                                     __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return true;
    IOLock *lock = transactionLock();
    if (lock) {
        Locked held(lock);
        runProbeLocked();
    } else IOLog("NABU BACKLIGHT gate=lock\n");
    __atomic_store_n(&started, 0, __ATOMIC_RELEASE);
    // An attempted probe is never a display startup prerequisite.
    return true;
#else
    return false;
#endif
}

extern "C" __attribute__((noinline)) int nabu_backlight_set_power(int on)
{
#if XNU_NABU_BACKLIGHT_POWER
    if (!bootArgEnabled("nabu-backlight") || !bootArgEnabled("nabu-backlight-power") ||
        !threadContext()) return nabu_backlight::PowerNotReady;
    IOLock *lock = __atomic_load_n(&powerLock, __ATOMIC_ACQUIRE);
    if (!lock) return nabu_backlight::PowerNotReady;
    Locked held(lock);
    // Re-check under the same mutex as probe; failed requalification cannot
    // leave a caller using stale readiness or another transaction's mapping.
    if (!ready) return nabu_backlight::PowerNotReady;
    Transaction transaction;
    if (!transaction.pins.base) return nabu_backlight::PowerNotReady;
    nabu_backlight::Bus<Pins> bus(transaction.pins);
    uint8_t before = 0, after = 0;
    bool ok = nabu_backlight::set_power(bus, on != 0, before, after);
    IOLog("NABU BACKLIGHT request=%s BL_EN=%#x->%#x result=%s bus-error=%u\n",
          on ? "on" : "off", before, after, ok ? "verified" : "failed", unsigned(bus.error()));
    if (!ok && on) {
        nabu_backlight::Bus<Pins> retry(transaction.pins);
        uint8_t againBefore = 0, againAfter = 0;
        ok = nabu_backlight::set_power(retry, true, againBefore, againAfter);
        IOLog("NABU BACKLIGHT recovery-to-on %s BL_EN=%#x->%#x bus-error=%u\n",
              ok ? "succeeded" : "failed", againBefore, againAfter, unsigned(retry.error()));
    }
    return ok ? nabu_backlight::PowerSuccess : nabu_backlight::PowerIoError;
#else
    (void)on;
    return nabu_backlight::PowerUnsupported;
#endif
}

#if XNU_NABU_BACKLIGHT_POWER
extern "C" __attribute__((noinline)) void nabu_backlight_power_transition(
    unsigned long ordinal, bool primary, int parentAck)
{
    // IOService's nonzero return is an ACK delay, not an ordinary error code.
    // The caller always returns its original parent's value unchanged.
    if (!primary || ordinal > 1 || parentAck != 0) {
        IOLog("NABU BACKLIGHT power-hook=skipped primary=%u ordinal=%lu parent-ack=%#x\n",
              unsigned(primary), ordinal, unsigned(parentAck));
        return;
    }
    const int status = nabu_backlight_set_power(ordinal != 0);
    IOLog("NABU BACKLIGHT power-hook ordinal=%lu result=%#x parent-ack=%#x runtime-qualified=0\n",
          ordinal, unsigned(status), unsigned(parentAck));
}
#endif
