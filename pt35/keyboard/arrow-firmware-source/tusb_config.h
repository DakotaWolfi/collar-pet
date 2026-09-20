#ifndef _TUSB_CONFIG_H_
#define _TUSB_CONFIG_H_

#include "debug_config.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CFG_TUSB_MCU        OPT_MCU_RP2040
#define CFG_TUSB_OS         OPT_OS_PICO
#define CFG_TUSB_RHPORT0_MODE OPT_MODE_DEVICE

#ifndef CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_SECTION
#endif

#ifndef CFG_TUSB_MEM_ALIGN
#define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))
#endif

// Device mode only, no host stack
#define CFG_TUD_ENABLED       1
#define CFG_TUD_ENDPOINT0_SIZE 64

// Two HID interfaces: keyboard (itf 0) and gamepad (itf 1), plus one CDC
// (virtual serial) interface used for debug logging over USB - only
// present when DEBUG_MATRIX is enabled.
#define CFG_TUD_HID           2
#define CFG_TUD_CDC           DEBUG_MATRIX
#define CFG_TUD_MSC           0
#define CFG_TUD_MIDI          0
#define CFG_TUD_VENDOR        0

#define CFG_TUD_HID_EP_BUFSIZE 16

#define CFG_TUD_CDC_RX_BUFSIZE 64
#define CFG_TUD_CDC_TX_BUFSIZE 64

#ifdef __cplusplus
}
#endif

#endif
