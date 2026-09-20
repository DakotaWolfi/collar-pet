#ifndef DEBUG_CONFIG_H_
#define DEBUG_CONFIG_H_

// Set to 1 to enable matrix-scan debug logging (prints "row=%d col=%d" for
// every new key press, over UART and a USB CDC serial port) and to add the
// CDC interface to the USB descriptor. Set to 0 to build without it - the
// device then only exposes the keyboard + gamepad HID interfaces.
#define DEBUG_MATRIX 0

#endif
