# Current prototype versions

Latest source sync from `temp resurces/current`:

- Orange Pi runtime (`pi/`): CollarPet Linux Brain `9.5.5-vu-gear` (from latest `collar pet` bundle)
- PetMind model resources (`pi/brain/`): V0.1 and V0.3 files synced (including 28-feature V0.3 schema/model artifacts)
- Remote firmware (`remote/`): `collarpet_remote_network_v4_6_0_active_gear`
- ESP32-S3 coprocessor (`esp32/`): `CollarPet_ESP32S3_v2_4_HotPlug_PPG`
- PM35/PT35 integration (`pt35/`): latest desktop/link update script set from current bundle
- PT35 keyboard resources (`pt35/Downloads/`, `pt35/keyboard/`): UF2 images, daemon helpers, and curated firmware source synced
- PT35 PetMind training UI (`pt35/training/`): `cp-petmind-training` launcher synced
- PetMind handoff scripts (`pi/scripts/`, `pt35/scripts/`): shared BLE scan fix and PT35 training shortcut/UI update helpers synced
- PetMind handoff archive (`artifacts/`): `CollarPet_PetMind_RealTraining_Prep_v1.zip`
- Fan controller (`tools/fan/`): CollarPet fan control v1

Repository files intentionally use stable filenames; history and exact revisions should be tracked through Git commits/tags rather than embedding versions in every filename.
