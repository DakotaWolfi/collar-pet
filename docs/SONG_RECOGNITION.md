# Song recognition

CollarPet contains a local known-song matcher. It recognises songs that have already been added to its fingerprint database without requiring a cloud music-recognition service.

This is separate from general speech or voice-command recognition.

## Processing flow

```text
microphone audio
      |
      v
short rolling audio window
      |
      +--> reject silence / near-silence
      |
      v
fingerprint extraction
      |
      v
lookup against local fingerprint index
      |
      v
candidate vote counts
      |
      v
acquire / hold / switch decision
      |
      +--> UI song announcement
      +--> remote state
      +--> optional known-song reactions
```

The current Linux runtime uses a six-second song window and attempts matching every two seconds. The database is split into a catalog, per-song fingerprints and an indexed representation for fast lookup.

## Audio gate

Very quiet or zero-filled audio is rejected before fingerprint matching. Otherwise normalised silence can create a repeatable artificial fingerprint and produce false recognitions. The current default gate is approximately `-60 dBFS`, configurable with `COLLARPET_SONG_GATE_DBFS`.

## Acquire and hold thresholds

Current defaults are:

```text
new-song acquire threshold: 70 votes
current-song hold threshold: 40 votes
```

A new identity must clear the stronger threshold. Once a song is already locked, the lower hold threshold helps it survive quiet passages or short weak sections without flickering away.

The thresholds are configurable with `COLLARPET_SONG_ACQUIRE_VOTES` and `COLLARPET_SONG_HOLD_VOTES`.

## Switching between songs

A different candidate is not allowed to replace the current song merely because it wins by one vote. The current defaults also require roughly a `1.20` score ratio and a `25` vote absolute gap before switching.

## User-visible behaviour

When a new known song is accepted, CollarPet can temporarily give the e-paper display to a full-screen announcement with artist, title and confidence information. The current announcement time is about two seconds.

A known song can also trigger higher-level reactions. One implemented example is the optional Tail Company known-song wag.

## Database structure

```text
songdb/
  songs.csv
  fingerprints/
  index/
    manifest.json
    sid_map.json
    unique_hashes.u32
    offsets.u32
    postings.u32
```

Generated index data is runtime data and does not need to be committed to the repository.

## Scope

The matcher is not intended to identify arbitrary unknown music from the internet. It recognises songs that were deliberately prepared and added to the local database.

Voice commands should remain a separate pipeline so speech recognition can be changed without disturbing the known-song matcher.
