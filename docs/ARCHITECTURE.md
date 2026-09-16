# System architecture

CollarPet is split deliberately between a Linux computer and a microcontroller. The goal is to keep computationally heavy or high-level work on Linux while leaving timing-sensitive and hardware-facing jobs to the ESP32-S3.

```text
                       +----------------------+
                       |   Orange Pi Zero 3W  |
                       |  Linux / pet runtime |
                       +----------+-----------+
                                  |
                         UART / local links
                                  |
                       +----------v-----------+
                       |      ESP32-S3        |
                       | real-time coprocessor|
                       +----------+-----------+
                                  |
                +-----------------+------------------+
                |        |        |        |         |
              LEDs     haptic    IMU      GPS      sensors

          LoRa remotes <------ CollarPet ------> BLE gear
                                         |          tail / ears
                                         +-------> phone coexistence
```

## Orange Pi responsibilities

The Orange Pi runs the main CollarPet process. Its responsibilities include higher-level behaviour, e-paper rendering, audio processing and song recognition, BLE gear control, remote state, logging, and the learned/personality layer.

The current Linux runtime is `pi/collarpet.py`.

## ESP32-S3 responsibilities

The ESP32-S3 remains a coprocessor rather than being replaced by Linux GPIO. It is intended to handle timing-sensitive hardware work including addressable LEDs, LRA haptics, ambient/environment sensing, IMU acquisition, GPS, pulse/contact preprocessing and future local inputs.

## Remote network

The e-paper remotes are Heltec Vision Master E213 boards using LoRa. One remote can act as master and the others can operate as nodes. The UI is intentionally pet-first rather than an engineering dashboard.

## BLE gear

Tail and EarGear are controlled over BLE when enabled. CollarPet is designed to coexist with a phone: connect when needed, hold while active, then release after idle time.

## Design principle

The collar is treated as one system. Sensor data, song recognition, remote input, pet state, lights, haptics and animatronic gear can all feed the same higher-level behaviour.
