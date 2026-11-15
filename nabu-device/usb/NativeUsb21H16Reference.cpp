/* Compile against the exact captured XNU recipe as a layout reference.
 * This verifies IOService's compilation layout and opaque storage alignment.
 * It does not emit or claim an IOUSBDeviceController vtable or runtime ABI.
 * Only the read-only evidence markers below may enter the platform archive.
 */
#ifndef NABU_USB_21H16_COMPILE_REFERENCE
#error "This translation unit is a compile-only reference"
#endif
#ifndef NABU_USB_21H16_SOURCE_ID
#error "Compile reference must bind the exact peripheral builder source inputs"
#endif
#include <IOKit/IOService.h>
#include "NabuUsb21H16Contract.h"
#include "NabuUsb21H16Observed.h"
static_assert(sizeof(IOService) == NabuUsb21H16::IOServiceBytes);
static_assert(NABU_USB_21H16_OBSERVED_CLASS_SIZE == NabuUsb21H16::ControllerBytes);
static_assert(NABU_USB_21H16_OBSERVED_SLOT_COUNT == NabuUsb21H16::ControllerSlots);
static_assert(NABU_USB_21H16_OBSERVED_PURE_COUNT == sizeof(NabuUsb21H16::PureSlots)/sizeof(uint32_t));
extern "C" {
extern const uint32_t nabu_usb_21h16_reference_policy[] = {1,0,552,234,18};
extern const char nabu_usb_21h16_reference_input[] = NABU_USB_21H16_INPUT_SHA256;
extern const char nabu_usb_21h16_reference_source[] = NABU_USB_21H16_SOURCE_ID;
}
