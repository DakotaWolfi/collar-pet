# BLE gear integration

CollarPet can control compatible Tail Company / TailControl tails and EarGear devices over Bluetooth Low Energy. This is an independent hobby integration and is not an official Tail Company product.

## Connection policy

CollarPet is designed to coexist with a phone. In automatic mode it connects when an action needs the device, keeps the connection while active, then releases after idle time. Manual Connect can hold the device; Release explicitly gives it back for phone use.

## Tail support

The current integration supports preset tail commands and experimental direct servo positioning where supported. Direct positioning is used by the experimental active mode and is intentionally updated conservatively rather than flooded over BLE.

An optional known-song reaction can issue a happy wag when the song matcher accepts a new known song.

## EarGear support

EarGear integration supports the current unified TailControl-style BLE profile and an older EarGear2 profile. Current actions include listen and tilt modes plus their stop commands.

## Learned-device state

Learned gear information belongs in runtime state and should not be committed to the public repository.
