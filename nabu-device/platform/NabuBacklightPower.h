// SPDX-License-Identifier: BSD-2-Clause-Patent
// Independent build gate for the optional, synchronous Legacy PM side effect.
#pragma once
#ifndef XNU_NABU_BACKLIGHT
#define XNU_NABU_BACKLIGHT 0
#endif
#ifndef XNU_NABU_BACKLIGHT_POWER
#define XNU_NABU_BACKLIGHT_POWER 0
#endif
#if XNU_NABU_BACKLIGHT != 0 && XNU_NABU_BACKLIGHT != 1
#error XNU_NABU_BACKLIGHT_must_be_zero_or_one
#endif
#if XNU_NABU_BACKLIGHT_POWER != 0 && XNU_NABU_BACKLIGHT_POWER != 1
#error XNU_NABU_BACKLIGHT_POWER_must_be_zero_or_one
#endif
#if XNU_NABU_BACKLIGHT_POWER && !XNU_NABU_BACKLIGHT
#error XNU_NABU_BACKLIGHT_POWER_requires_probe
#endif

#if XNU_NABU_BACKLIGHT_POWER
// Called after the original parent's synchronous ACK only. Never changes or
// acknowledges the original PM state. Nonzero parent ACKs (including delayed
// completion budgets) are observed and skipped, with no invented continuation.
extern "C" void nabu_backlight_power_transition(unsigned long ordinal, bool primary, int parentAck);
#endif
