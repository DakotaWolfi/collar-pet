# ESP32-S3 coprocessor

The ESP32-S3 is a deliberate part of the architecture, not a temporary substitute for Linux GPIO.

Linux handles high-level processing. The ESP32-S3 handles timing-sensitive and hardware-facing work such as LEDs, haptics, sensors, GPS and local inputs.

## Pi link

The prototype uses a UART link between the Orange Pi and ESP32-S3. The Linux side serialises outgoing traffic so concurrent high-level tasks do not write over one another.

The protocol is intentionally lightweight: the ESP32 does not need the whole CollarPet state model, only hardware-oriented commands and compact telemetry messages.

## Why keep both processors?

The split provides a useful failure boundary and keeps future SBC revisions flexible. Linux-side changes do not require all real-time hardware drivers to be redesigned around a non-real-time OS.
