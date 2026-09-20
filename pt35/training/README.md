# PT35 PetMind Training UI

Included script:

- `cp-petmind-training`

This is the PT35 standalone training interface used to drive `cp-petmind-record` over Wi-Fi/SSH with BLE marker fallback.

## Expected target paths

On PT35 host:

- `~/.local/bin/cp-petmind-training`
- `~/.config/collarpet/link.conf`

## Behavior summary

- transport modes: `AUTO`, `Wi-Fi`, `BLE`
- context labels and instant event markers
- live status card polling (Wi-Fi path)
- explicit BLE fallback state: `SENT / UNCONFIRMED`
- rescue AP start/stop hooks via `cp-rescue`

See repository documentation for end-to-end workflow details:
- `docs/PETMIND_REAL_TRAINING.md`
