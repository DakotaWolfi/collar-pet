# CollarPet documentation

This directory contains the longer-form documentation for CollarPet. The top-level README is intentionally kept as an overview; the files here describe the individual subsystems in more detail.

## System documentation

- [Architecture](ARCHITECTURE.md)
- [Song recognition](SONG_RECOGNITION.md)
- [Remote network](REMOTE_NETWORK.md)
- [BLE gear](BLE_GEAR.md)
- [ESP32-S3 coprocessor](ESP32_COPROCESSOR.md)
- [Thermal management](THERMAL_MANAGEMENT.md)
- [Hardware status](HARDWARE_STATUS.md)
- [Luma voice commands](VOICE_COMMANDS_PLAN.md)
- [Music and reactive gear](MUSIC_REACTIONS.md)
- [PetMind real-world training](PETMIND_REAL_TRAINING.md)

The project is under active development, so these documents describe the current prototype rather than a frozen specification.

## Source bundles published in Git

Latest source snapshots from the temporary `current` bundle are now tracked in:

- `pi/` for the Orange Pi runtime
- `remote/` for Heltec remote firmware
- `esp32/` for ESP32-S3 coprocessor firmware
- `pt35/` for PM35/PT35 integration scripts and launchers
- `pt35/Downloads/` for PT35 keyboard/button helper artifacts
- `pt35/keyboard/` for curated keyboard firmware source
- `pt35/training/` for PT35 PetMind training launcher script

For now, local-only or heavy temporary assets (embedded SDK clones, temporary `.git` folders, runtime state files, local venvs) remain excluded from Git.
