/* Self-authored compile-reference contract from the pinned 21H16 original.
 * Full input and derivation identity are in the private controller-abi receipt.
 * No guessed Apple method signatures or original virtual addresses are exposed.
 */
#pragma once
#include <stdint.h>
#ifndef NABU_USB_NATIVE_21H16
#define NABU_USB_NATIVE_21H16 0
#endif
#if NABU_USB_NATIVE_21H16 != 0
#error "21H16 USB runtime signatures, dependencies and ownership remain unqualified"
#endif
namespace NabuUsb21H16 {
inline constexpr uint32_t ControllerBytes = 552;
inline constexpr uint32_t IOServiceBytes = 136;
inline constexpr uint32_t ControllerSlots = 234;
inline constexpr uint32_t IOServiceSlots = 168;
inline constexpr uint32_t PureSlots[] = {
    175,177,179,180,181,182,183,184,185,186,187,188,189,192,193,199,202,227
};
/* This storage is not an Apple class and cannot be used as one. */
struct ControllerStorage { alignas(8) uint8_t bytes[ControllerBytes]; };
static_assert(sizeof(ControllerStorage) == ControllerBytes);
}
