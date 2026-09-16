# Music and reactive gear

CollarPet has an experimental mode that uses the microphone pipeline to make TailGear and EarGear react to music.

The first prototype directly mapped VU amplitude to servo movement. It worked and looked suitably ridiculous, but it mostly behaved like a furry analog meter: louder audio simply produced larger and faster movement.

The current development controller instead tries to infer the **physical character** of the audio envelope and choose a different movement vocabulary.

## Important scope

The current classifier does **not** claim to identify genre, musical emotion, harmony or semantic meaning.

With the signals currently available in the runtime, the honest information is closer to:

- how strong the audio is
- how steady or variable the envelope is
- whether sharp transients are occurring
- whether peaks recur frequently
- whether speech seems to dominate the current audio

The resulting labels describe how the sound *moves*, not what the song *means*.

## Inputs

The controller builds on audio features that are already produced by CollarPet:

```text
audio.level
audio.peak_level
audio.music_confidence
audio.speech_confidence
```

A short rolling history is used to calculate:

- smoothed level
- mean level
- envelope variance
- positive transients
- recurring peak rate

## Current states

### CALM

Low, steady energy.

Typical motion:

- tail: slow breathing sway
- ears: relaxed pose with occasional soft tilt

### FLOWING

Moderate energy without strong repeated transients.

Typical motion:

- tail: smooth lazy sweep
- ears: slowly look/listen around

### BOUNCY

Recurring envelope peaks with moderate variation.

Typical motion:

- tail: compact happy bounce with center pauses
- ears: alternating perk pattern

### PUNCHY

Strong isolated transients.

Typical motion:

- tail: mostly poised, then deliberate flicks on hits
- ears: sharper perk/flick reactions on hits

### ENERGETIC

High sustained energy and/or strong variation.

Typical motion:

- tail: broad animated pattern
- ears: more varied active poses

## Why this is separate from song recognition

Known-song recognition answers:

> Which prepared song does this fingerprint match?

Music-feel analysis answers something closer to:

> What is the current physical character of the audio?

A song does not need to exist in the fingerprint database for reactive gear to work.

## Why this is separate from Luma

Luma uses the microphone for speech recognition and explicit commands.

Reactive gear is continuous audio behaviour. It should not reinterpret arbitrary speech as commands.

The shared microphone architecture is therefore:

```text
microphone
   |
   +--> known-song matcher
   |
   +--> Luma speech recogniser
   |
   +--> generic music detector
   |
   +--> music-feel tracker
             |
             +--> TailGear motion vocabulary
             +--> EarGear motion vocabulary
```

## Enabling active gear

Current Luma commands include:

```text
Luma, active gear on
Luma, active gear off
```

Aliases such as `gear V U on` are also accepted by the experimental recogniser.

While active gear owns the devices, CollarPet keeps the relevant BLE connections instead of releasing them to the phone after the normal idle timeout.

## Ear-only music reaction

There is also a simpler optional EarGear music reaction:

```text
Luma, ears music on
Luma, ears music off
```

This is useful for experimenting with ear movement separately from the full tail+ear active controller.

## Logging

The music-feel tracker emits lines such as:

```text
[MUSIC FEEL] FLOWING level=0.24 mean=0.21 var=0.04 pulse=0.50/s peak=0
[MUSIC FEEL] BOUNCY level=0.31 mean=0.27 var=0.08 pulse=1.25/s peak=1
[MUSIC FEEL] PUNCHY level=0.61 mean=0.33 var=0.12 pulse=1.25/s peak=1
```

A useful combined watcher is:

```bash
tail -F /home/jenna/collarpet/logs/collarpet-console.log \
  | grep --line-buffered -Ei '\[LUMA|\[SONG|\[MUSIC|\[MUSIC FEEL|\[EARS|\[TAIL|\[GEAR VU|AUDIO'
```

## Current tuning philosophy

The direct servo path is intentionally rate-limited. The goal is expressive movement, not maximum update rate.

The current classifier should be treated as an experimental heuristic. Real-world testing with different music, crowd noise, speech, blower noise and collar placement will determine whether the state boundaries and movement vocabularies need adjustment.

A future version could add real onset/beat analysis, spectral-band energy and longer-term rhythm tracking. Those would allow more musical timing without pretending that raw amplitude alone is a beat detector.
