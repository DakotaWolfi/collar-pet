#include <string.h>
#include "usb_descriptors.h"

//--------------------------------------------------------------------+
// Device Descriptor
//--------------------------------------------------------------------+
// NOTE: 0xCafe is TinyUSB's example VID, safe for private/hobby use on your
// own machine but not for redistribution. Get a real VID:PID (e.g. from
// pid.codes) if you plan to ship this.
#define USB_VID   0xCafe
#define USB_PID   0x4011
#define USB_BCD   0x0200

tusb_desc_device_t const desc_device = {
    .bLength            = sizeof(tusb_desc_device_t),
    .bDescriptorType    = TUSB_DESC_DEVICE,
    .bcdUSB             = USB_BCD,

    .bDeviceClass       = 0x00,
    .bDeviceSubClass    = 0x00,
    .bDeviceProtocol    = 0x00,
    .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,

    .idVendor           = USB_VID,
    .idProduct          = USB_PID,
    .bcdDevice          = 0x0100,

    .iManufacturer      = 0x01,
    .iProduct           = 0x02,
    .iSerialNumber      = 0x03,

    .bNumConfigurations = 0x01
};

uint8_t const *tud_descriptor_device_cb(void) {
    return (uint8_t const *) &desc_device;
}

//--------------------------------------------------------------------+
// HID Report Descriptors
//--------------------------------------------------------------------+

// Keyboard interface: two report IDs multiplexed on one IN endpoint -
// standard 6-key-rollover keyboard, plus consumer control (media keys).
uint8_t const desc_hid_report_keyboard[] = {
    TUD_HID_REPORT_DESC_KEYBOARD(HID_REPORT_ID(REPORT_ID_KEYBOARD)),
    TUD_HID_REPORT_DESC_CONSUMER(HID_REPORT_ID(REPORT_ID_CONSUMER_CONTROL))
};

// Gamepad interface: two report IDs multiplexed on one IN endpoint - the
// gamepad report (int8 x,y,z,rz,rx,ry; uint8 hat; uint32 buttons), plus a
// standard mouse report used when mouse mode is active (see main.c).
uint8_t const desc_hid_report_gamepad[] = {
    TUD_HID_REPORT_DESC_GAMEPAD(HID_REPORT_ID(REPORT_ID_GAMEPAD)),
    TUD_HID_REPORT_DESC_MOUSE(HID_REPORT_ID(REPORT_ID_MOUSE))
};

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
    if (instance == HID_ITF_KEYBOARD) {
        return desc_hid_report_keyboard;
    } else {
        return desc_hid_report_gamepad;
    }
}

//--------------------------------------------------------------------+
// Configuration Descriptor
//--------------------------------------------------------------------+

#define EPNUM_KEYBOARD_IN 0x81
#define EPNUM_GAMEPAD_IN  0x82
#if DEBUG_MATRIX
#define EPNUM_CDC_NOTIF   0x83
#define EPNUM_CDC_OUT     0x03
#define EPNUM_CDC_IN      0x84
#endif

#if DEBUG_MATRIX
#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_HID_DESC_LEN * 2 + TUD_CDC_DESC_LEN)
#else
#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_HID_DESC_LEN * 2)
#endif

uint8_t const desc_configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN,
                           TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),

    // Interface 0: Keyboard HID, boot protocol, polled at 1ms
    TUD_HID_DESCRIPTOR(ITF_NUM_KEYBOARD, 0, HID_ITF_PROTOCOL_KEYBOARD,
                        sizeof(desc_hid_report_keyboard), EPNUM_KEYBOARD_IN,
                        CFG_TUD_HID_EP_BUFSIZE, 1),

    // Interface 1: Gamepad HID, generic protocol, polled at 4ms
    TUD_HID_DESCRIPTOR(ITF_NUM_GAMEPAD, 0, HID_ITF_PROTOCOL_NONE,
                        sizeof(desc_hid_report_gamepad), EPNUM_GAMEPAD_IN,
                        CFG_TUD_HID_EP_BUFSIZE, 4),

#if DEBUG_MATRIX
    // Interface 2+3: CDC (virtual serial), used for debug logging over USB
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, 6, EPNUM_CDC_NOTIF, 8,
                        EPNUM_CDC_OUT, EPNUM_CDC_IN, 64),
#endif
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
    (void) index;
    return desc_configuration;
}

//--------------------------------------------------------------------+
// String Descriptors
//--------------------------------------------------------------------+

char const *string_desc_arr[] = {
    (const char[]) {0x09, 0x04}, // 0: supported language = English (0x0409)
    "pocketTerm35",              // 1: Manufacturer
    "pocketTerm35 Keyboard+Gamepad", // 2: Product
    "000001",                    // 3: Serial
    "pocketTerm35 Keyboard",     // 4: Keyboard interface name (unused, kept for reference)
    "pocketTerm35 Gamepad",      // 5: Gamepad interface name (unused, kept for reference)
#if DEBUG_MATRIX
    "pocketTerm35 Debug",        // 6: CDC interface name
#endif
};

static uint16_t _desc_str[32];

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void) langid;
    size_t chr_count;

    if (index == 0) {
        memcpy(&_desc_str[1], string_desc_arr[0], 2);
        chr_count = 1;
    } else {
        if (index >= sizeof(string_desc_arr) / sizeof(string_desc_arr[0])) {
            return NULL;
        }

        const char *str = string_desc_arr[index];
        chr_count = strlen(str);
        size_t const max_count = sizeof(_desc_str) / sizeof(_desc_str[0]) - 1;
        if (chr_count > max_count) chr_count = max_count;

        for (size_t i = 0; i < chr_count; i++) {
            _desc_str[1 + i] = str[i];
        }
    }

    _desc_str[0] = (uint16_t) ((TUSB_DESC_STRING << 8) | (2 * chr_count + 2));

    return _desc_str;
}
