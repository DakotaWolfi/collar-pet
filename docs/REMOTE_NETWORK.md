# Remote network

CollarPet uses Heltec Vision Master E213 e-paper boards as handheld remotes. They communicate using LoRa.

The current firmware is in `remote/collarpet_remote.ino`.

## Roles

The firmware supports undecided, master and node roles. A master coordinates the small remote network; nodes learn the master and report their presence.

## Current radio configuration

The prototype firmware currently centralises an EU868-oriented configuration around 869.525 MHz, 125 kHz bandwidth, spreading factor 8, coding rate 7 and 14 dBm transmit power. These are prototype settings, not a permanent radio specification.

## UI philosophy

The normal remote screen is deliberately not a diagnostics display. Current goals are a large pet portrait, short state/event text, one compact LoRa strength indicator, contextual gear indicators and warnings only when they are genuinely useful.

Setup and engineering details belong in menus rather than occupying the normal screen.

## Hardware variants

The source deliberately retains compile-time display/hardware options because not every remote is guaranteed to be the same board revision.
