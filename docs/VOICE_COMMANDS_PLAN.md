# Luma — local voice commands

> **Status:** implemented / experimental.

**Luma** is CollarPet's local voice-command interface. It runs locally on the Orange Pi and shares the existing microphone stream with song recognition instead of opening a second ALSA capture device.

The current implementation uses Vosk with a restricted grammar. That is deliberate: physical actions such as moving animatronic gear should come from a small, explicit command set rather than unconstrained speech interpretation.

## Pipeline

```text
microphone stream
      |
      +---------------------------> known-song matcher
      |
      +---------------------------> audio / music analysis
      |
      v
16 kHz speech stream
      |
      v
Vosk restricted grammar
      |
      v
Luma wake stage
      |
      v
short command window
      |
      v
CollarPet action dispatcher
      |
      +--> tail / ears
      +--> LEDs / haptics
      +--> pet state / display
      +--> BLE gear connection
```

## Two-stage wake behaviour

Luma accepts both a single utterance:

```text
Luma, tail happy
```

and a more natural pause after the wake name:

```text
Luma
<pause>
tail happy
```

After the wake name is accepted, a short command window opens. Bare commands outside that window are ignored.

The recogniser also contains a small number of word-order aliases for phrases that Vosk commonly reverses, for example `happy tail` -> `tail happy`.

## Wake feedback

Two optional reactions can acknowledge the wake name before the full command has finished:

- collar haptic click
- short EarGear twitch

The ear reaction is independently switchable because it requires CollarPet to take BLE ownership of the ears.

```text
Luma, haptic feedback on
Luma, haptic feedback off
Luma, ears react on
Luma, ears react off
```

## Current command groups

### Tail

```text
Luma, tail happy
Luma, tail home
Luma, tail shy
```

### EarGear

```text
Luma, ears center
Luma, ears perk
Luma, ears relax
Luma, ears left
Luma, ears right
Luma, ears twitch
Luma, ears wiggle
Luma, ears listen
Luma, ears tilt
Luma, ears stop
```

### Gear connection

```text
Luma, connect tail
Luma, connect ears
Luma, connect gear
```

`connect gear` requests both learned BLE devices through the normal gear managers rather than bypassing the normal connection policy.

### Reactive gear

```text
Luma, ears music on
Luma, ears music off
Luma, active gear on
Luma, active gear off
```

Active gear uses the audio-analysis pipeline to animate tail and ears. The current development version classifies the physical character of the music before selecting movement patterns. See [Music and reactive gear](MUSIC_REACTIONS.md).

### Collar / pet

```text
Luma, lights on
Luma, lights off
Luma, stealth on
Luma, stealth off
Luma, attention
Luma, wake up
Luma, calm down
Luma, status
```

## Runtime and model

The current prototype uses the Vosk small English model and resamples the already-selected microphone channel to 16 kHz for speech recognition.

The speech recogniser does not open its own ALSA device. This avoids fighting the existing song-recognition path for microphone ownership.

## Logging

Luma diagnostics go to the CollarPet console log. A useful combined development filter is:

```bash
tail -F /home/jenna/collarpet/logs/collarpet-console.log \
  | grep --line-buffered -Ei '\[LUMA|\[SONG|\[MUSIC|\[MUSIC FEEL|\[EARS|\[TAIL|\[GEAR VU|AUDIO'
```

This makes voice recognition, known-song matching, generic music detection and gear reactions visible together.

## Current limitations

- Recognition quality depends strongly on the collar microphone, blower noise and surrounding speech.
- The wake name currently uses Vosk grammar aliases rather than a dedicated wake-word engine.
- There is no TTS response yet.
- The grammar is intentionally restricted.
- Luma is experimental and the command vocabulary may change.
