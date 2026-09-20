# PT35 PetMind Handoff Scripts

This folder contains PT35-targeted scripts from the PetMind real-world training handoff.

## Scripts

- `add-petmind-training-desktop-shortcut.sh`
  - Device: PT35
  - Run as: normal user (no sudo)
  - Purpose: create desktop/application launcher entries for `~/.local/bin/cp-petmind-training`
  - Rollback: remove generated `.desktop` files from desktop and `~/.local/share/applications/`

- `update-pt35-petmind-training-ui-v2.sh`
  - Device: PT35
  - Run as: normal user (no sudo)
  - Purpose: replace `~/.local/bin/cp-petmind-training` with UI V2 implementation
  - Built-in backup: `~/.local/share/collarpet-training-backups/cp-petmind-training.<timestamp>.bak`
  - Rollback: restore the latest backup and set executable mode

## Notes

- These scripts are for deployment/update on a live PT35 host.
- They are not expected to run in this repository checkout on Windows.
