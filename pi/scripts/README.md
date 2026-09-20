# Orange Pi Runtime Patch Scripts

This folder contains collar-side runtime patch scripts from the PetMind training handoff.

## Scripts

- `update-collarpet-shared-ble-scanfix.sh`
  - Device: Orange Pi (CollarPet host)
  - Run as: root via sudo
  - Usage: `sudo bash update-collarpet-shared-ble-scanfix.sh collarpet`
  - Purpose: patch runtime remote-connect BLE handling so shared-broker mode keeps the scan subscription active
  - Built-in backup path: `/var/backups/collarpet-shared-ble-scanfix/`
  - Rollback: `sudo bash update-collarpet-shared-ble-scanfix.sh collarpet --rollback`

## Notes

- Script expects runtime at `/home/jenna/collarpet/collarpet.py` and service `collarpet.service`.
- Script validates syntax and attempts service health checks after patching.
