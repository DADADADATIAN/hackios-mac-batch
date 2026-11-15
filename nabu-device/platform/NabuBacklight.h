// SPDX-License-Identifier: BSD-2-Clause-Patent
// KTZ8866 byte-register control over bit-banged open-drain I2C on TLMM GPIOs.
//
// Ported from the donor GPU242 project (iPadOS 16.5/20F66):
//   donor commit cb45dcf71cd7381c1d39966e411c3ed69a512fbf
//   donor path   patches/nabu-display/NabuBacklight.h
// Local changes: hardcoded 0x22/0x23 wire bytes are derived from the generated
// I2cAddress instead, constants come from NabuBacklightResources.h, and a
// read-only probe() self-test was added. The Bus/Pins contract and the
// read-modify-write set_power() structure follow the donor file.
#pragma once
#include <stdint.h>
#include "NabuBacklightResources.h"
#include "NabuBacklightPower.h"

namespace nabu_backlight {
enum Error { Ok, BusNotIdle, ClockHeldLow, AddressNack, RegisterNack, ReadAddressNack, DataNack };

// The Pins contract: take()/restore() save and hand back both pins' TLMM
// config and output words, line(scl, high) releases (high) or drives low, and
// level() samples the input bit. begin() releases both lines before checking
// idle, so the check sees the electrical bus after our own release; a held-low
// line still means another owner or a fault and the transaction aborts with
// the saved configuration restored.
template<class Pins> class Bus {
    Pins &pins;
    Error failure = Ok;
    void delay() { pins.delay_us(5); }
    bool clock_high() {
        pins.line(true, true);
        for (unsigned i = 0; i < 100; ++i) {
            if (pins.level(true)) return true;
            delay();
        }
        failure = ClockHeldLow;
        return false;
    }
    bool start() {
        pins.line(false, true); delay();
        if (!clock_high()) return false;
        delay(); pins.line(false, false); delay(); pins.line(true, false); delay();
        return true;
    }
    bool stop() {
        pins.line(true, false); pins.line(false, false); delay();
        bool ok = clock_high(); delay(); pins.line(false, true); delay();
        return ok;
    }
    bool bit(bool out, bool &in) {
        pins.line(false, out); delay();
        if (!clock_high()) return false;
        delay(); in = pins.level(false); pins.line(true, false); delay();
        return true;
    }
    bool write(uint8_t value, Error nack) {
        bool sample;
        for (int i = 7; i >= 0; --i)
            if (!bit((value >> i) & 1, sample)) return false;
        if (!bit(true, sample)) return false;
        if (sample) { failure = nack; return false; }
        return true;
    }
    bool read(uint8_t &value) {
        value = 0;
        bool sample;
        for (unsigned i = 0; i < 8; ++i) {
            if (!bit(true, sample)) return false;
            value = uint8_t(value << 1) | sample;
        }
        return bit(true, sample); // final byte: NACK, then STOP
    }
    bool begin() {
        failure = Ok;
        pins.take(); pins.line(false, true); pins.line(true, true); delay();
        if (!pins.level(false) || !pins.level(true)) {
            failure = BusNotIdle; pins.restore(); return false;
        }
        return true;
    }
    bool finish(bool ok) {
        bool stopped = stop(); pins.restore(); return ok && stopped;
    }
public:
    explicit Bus(Pins &io) : pins(io) {}
    bool read_register(uint8_t reg, uint8_t &value) {
        if (!begin()) return false;
        bool ok = start() && write(AddressWrite, AddressNack) && write(reg, RegisterNack)
            && start() && write(AddressRead, ReadAddressNack) && read(value);
        return finish(ok);
    }
    bool write_register(uint8_t reg, uint8_t value) {
        if (!begin()) return false;
        bool ok = start() && write(AddressWrite, AddressNack) && write(reg, RegisterNack)
            && write(value, DataNack);
        return finish(ok);
    }
    Error error() const { return failure; }
};

// BL_EN bit 6 gates the LED output independently of LCD bias and brightness.
// Read-modify-write preserves the firmware's current-sink selection. Leave
// 0x04/0x05 intact so repeated off/on cycles restore the exact previous level.
// The separately compiled power hook uses this; the probe never calls it.
template<class Registers>
bool set_power(Registers &io, bool on, uint8_t &before, uint8_t &after) {
    if (!io.read_register(EnableRegister, before)) return false;
    uint8_t wanted = on ? (before | EnableBitMask) : (before & ~EnableBitMask);
    if (wanted != before && !io.write_register(EnableRegister, wanted)) return false;
    return io.read_register(EnableRegister, after) && after == wanted;
}

struct Probe {
    bool idle = false;
    uint8_t reg08 = 0;
    Error error = Ok;
};

// IOReturn values used by the exported entry points, spelled as literals so the
// compiled-out translation unit does not need IOKit headers. These match XNU's
// kIOReturn encoding.
enum IOReturnValues : int {
    PowerSuccess = 0x00000000,
    PowerNotReady = static_cast<int>(0xe00002cd),
    PowerIoError = static_cast<int>(0xe00002cc),
    PowerUnsupported = static_cast<int>(0xe00002c7),
};

// Read-only self-test: one bus transaction that reads the enable register and
// restores the pins. It never writes, so it cannot change panel state; a held
// bus ends the probe before any START is issued.
template<class Pins>
Probe probe(Pins &pins) {
    Probe result;
    Bus<Pins> bus(pins);
    result.idle = bus.read_register(EnableRegister, result.reg08);
    result.error = bus.error();
    return result;
}
} // namespace nabu_backlight

// Compile with XNU_NABU_BACKLIGHT=1 and request nabu-backlight=1 at boot for
// the register-read probe (the GPIO protocol still writes TLMM). The power
// primitive additionally requires XNU_NABU_BACKLIGHT_POWER=1, both boot args,
// thread context, and the most recent serialized probe's success. No runtime
// lock-screen/wake qualification is implied by compiling the hook.
extern "C" bool nabu_backlight_start(void);
extern "C" int nabu_backlight_set_power(int on);
