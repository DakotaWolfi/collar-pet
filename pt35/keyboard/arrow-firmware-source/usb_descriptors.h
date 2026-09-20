#ifndef USB_DESCRIPTORS_H_
#define USB_DESCRIPTORS_H_

#include "debug_config.h"
#include "tusb.h"

// Interface indices (order they are added to the configuration descriptor).
// CDC (debug serial, only present when DEBUG_MATRIX is enabled) takes two
// interface numbers (control + data).
enum {
    ITF_NUM_KEYBOARD = 0,
    ITF_NUM_GAMEPAD,
#if DEBUG_MATRIX
    ITF_NUM_CDC,
    ITF_NUM_CDC_DATA,
#endif
    ITF_NUM_TOTAL
};

// Report IDs used within the keyboard HID interface's report descriptor.
enum {
    REPORT_ID_KEYBOARD = 1,
    REPORT_ID_CONSUMER_CONTROL,
};

// Report IDs used within the gamepad HID interface's report descriptor.
// Mouse mode (see main.c) reuses this interface via REPORT_ID_MOUSE instead
// of adding a third HID interface.
enum {
    REPORT_ID_GAMEPAD = 1,
    REPORT_ID_MOUSE,
};

// tud_hid instance numbers (matches order tud_hid is enumerated by TinyUSB,
// i.e. order of interfaces with class = HID)
#define HID_ITF_KEYBOARD 0
#define HID_ITF_GAMEPAD  1

#endif
