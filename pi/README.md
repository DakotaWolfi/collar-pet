# CollarPet Orange Pi Runtime

This folder contains the latest synced Orange Pi runtime source from the `temp resurces/current/collar pet` bundle.

## Main entry point

- `collarpet.py`

## Additional runtime modules

- `collarpet_ble_client.py`
- `luma_voice.py`
- `songlib.py`
- `epaper_driver_select.py`
- `epaper_driver_v2.py`
- `epaper_driver_v4.py`
- `brain/petmind_nn_v0_1.py`
- `brain/petmind_nn_v0_3.py`
- `brain/petmind_schema_v0_3.py`
- `brain/petmind_v0_3.onnx`
- `brain/petmind_v0_3.pt`
- `assets/wolves/*.png`

## Python dependencies (runtime)

- `numpy`
- `pyserial`
- `Pillow`
- `bleak` (when not using the local BLE broker)
- `vosk` (voice commands)
- `waveshare-epaper` / Waveshare `waveshare_epd` module for your panel

## External resources expected by the runtime

- Song DB at `/home/jenna/collarpet/songdb`
- Voice model at `/home/jenna/collarpet/models/vosk-model-small-en-us-0.15`
- Runtime state and logs under `/home/jenna/collarpet/state` and `/home/jenna/collarpet/logs`

These external resources are currently not versioned in Git.
