# BLE gear integration

CollarPet can control compatible Tail Company / TailControl tails and EarGear devices over Bluetooth Low Energy. This is an independent hobby integration and is not an official Tail Company product.

## Connection policy

CollarPet is designed to coexist with a phone. In automatic mode it connects when an action needs the device, keeps the connection while active, then releases after idle time.

Manual Connect can hold the device. Release explicitly gives it back for phone use.

Luma can request the same connection actions:

```text
Luma, connect tail
Luma, connect ears
Luma, connect gear
```

These commands go through the existing BLE gear managers rather than bypassing their ownership policy.

## Tail support

The current integration supports preset tail commands and experimental direct servo positioning where supported.

Direct positioning is used by active/reactive modes and is intentionally rate-limited rather than flooded over BLE.

An optional known-song reaction can issue a happy wag when the song matcher accepts a new known song.

## EarGear support

EarGear integration supports the current unified TailControl-style BLE profile and an older EarGear2 profile.

Current actions include listen and tilt modes, named direct-position presets, and short movement sequences such as twitch and wiggle.

The current named movement layer includes:

```text
center
perk
relax
left
right
twitch
wiggle
```

This keeps raw servo-position details out of higher-level actions.

## Luma wake reaction

EarGear can optionally acknowledge the Luma wake name with a short twitch.

This is switchable because every wake reaction may require CollarPet to take BLE ownership of the ears:

```text
Luma, ears react on
Luma, ears react off
```

## Music-reactive gear

The current development runtime can keep tail and ears connected and animate them from the microphone analysis pipeline.

The first version simply mapped VU amplitude to movement. The newer controller instead estimates a physical music feel and selects different motion vocabularies.

See [Music and reactive gear](MUSIC_REACTIONS.md).

## Learned-device state

Learned gear information belongs in runtime state and should not be committed to the public repository.

The normal learned-device file is:

```text
/home/jenna/collarpet/state/gear.json
```
