# Luma — voice commands design plan

> **Status:** planned / experimental. Luma is not yet part of the stable CollarPet runtime.

**Luma** is the name of CollarPet's local voice-command interface. The name also fits the hardware rather well: CollarPet already has a substantial amount of addressable lighting, so Luma may eventually be quite capable of illuminating a room as well as listening to commands.

Luma is the next audio subsystem planned after the known-song matcher. It should be implemented as a separate pipeline rather than bolted directly into song recognition.

## First goals

The first version should be local, predictable and deliberately small in scope. Example commands might be:

```text
"Luma, tail happy"
"Luma, tail stop"
"Luma, ears listen"
"Luma, ears stop"
"Luma, lights off"
"Luma, stealth"
"Luma, status"
```

## Proposed pipeline

```text
microphone stream
      |
      +---------------------------> existing song matcher
      |
      v
voice activity / speech gate
      |
      v
Luma wake-word / command trigger
      |
      v
local speech recognition
      |
      v
strict command parser
      |
      v
CollarPet action dispatcher
      |
      +--> tail / ears
      +--> LEDs / haptics
      +--> pet state / display
```

Voice should go through the same action layer as remote/menu commands instead of becoming a second collection of direct hardware hacks.

## First bench test

The first experiment should answer three questions:

1. Can the Orange Pi run a local recogniser fast enough alongside the existing workload?
2. How well does it work with the real collar microphone, blower and background noise?
3. Can audio capture be shared cleanly with song recognition without ALSA-device conflicts?

Once those are answered, the recogniser and wake-word strategy can be selected and integrated.
