# PetMind Real-World Training (CollarPet + PT35)

Status: implementation guide based on 2026-09-20 handoff. Some steps still require hardware validation.

## 1. Architecture

- Orange Pi is the main controller and training/recording host.
- ESP32-S3 remains a coprocessor for sensors, LEDs, haptics, GPS, battery, and UART.
- Shared BLE broker owns HCI/BlueZ using service `collarpet-ble-broker.service` and socket `/run/collarpet-ble/broker.sock`.
- Main runtime BLE client path: `/home/jenna/collarpet/collarpet_ble_client.py`.

Important behavior in shared-broker mode:
- Runtime scan subscription must remain active even while PT35/remote GATT link is connected.
- Broker may briefly stop physical scan around connect, then resume.

## 2. Real-World Recorder

Expected recorder command:

```bash
/usr/local/bin/cp-petmind-record
```

Primary commands:

```bash
cp-petmind-record status
cp-petmind-record start
cp-petmind-record stop
cp-petmind-record clear
cp-petmind-record label speech 30
cp-petmind-record event bang
```

Data location on collar:

```text
/home/jenna/collarpet/petmind-data/
```

Suggested filename format:

```text
YYYYMMDD-HHMMSS-petmind-real.jsonl
```

## 3. PT35 Training Workflow

Standalone PT35 launcher script is included in this repo at:

- `pt35/training/cp-petmind-training`

On target PT35, expected install path is:

- `~/.local/bin/cp-petmind-training`

UI behavior (V2 intent):
- REC: `RECORDING` / `STOPPED`
- LABEL: active context and remaining seconds
- FRAMES
- LINK: Wi-Fi / BLE fallback / offline
- FILE: current JSONL basename
- LAST: most recent command/result/error

Status polling target: about every 2 seconds when reachable via Wi-Fi/recovery AP.

## 4. Recovery AP Workflow

Recovery helper expected on PT35:

```bash
~/.local/bin/cp-rescue
```

Expected SSID:

```text
CollarPet-Service
```

Use recovery AP when normal WLAN path fails. This restores SSH/status feedback and is preferred over BLE-only fallback.

## 5. BLE Fallback Protocol

AUTO transport order:
1. normal Wi-Fi or recovery AP
2. SSH into collar
3. run `cp-petmind-record`
4. fallback to BLE marker send

BLE fallback rules:
- uses existing environmental scanner path
- does not open another GATT connection
- one-way PT35 to collar
- UI must show `SENT / UNCONFIRMED`
- no receipt acknowledgement yet

Marker concept:
- manufacturer ID `0xFFFF`
- magic `CPTR`
- version/sequence/command/argument/duration
- sequence used for duplicate suppression

## 6. Data Format Expectations

Recorder cadence target: about every 0.5 s.

Each record should include:
- all 28 V0.3 features
- model prediction, confidence, top predictions
- current human context label
- world mood/activity/reason
- timestamps

V0.3 feature/expression schema source is included at:
- `pi/brain/petmind_schema_v0_3.py`

## 7. Field Collection Procedure

Recommended first sessions:
- quiet/resting
- walking
- speech
- music
- machinery/noise
- crowded BLE
- EF badges nearby
- fox approach/leave cycles
- isolated sudden events

Always inspect the first JSONL capture before collecting large datasets.

## 8. Validation / Test Procedure

Run before declaring the workflow complete:

1. Shared BLE:
   - keep remote connected
   - confirm both EF28 badges remain visible
   - ensure no new scanner pause regression in shared-broker mode
2. Recorder:
   - start
   - `label speech 20`
   - wait
   - status
   - stop
   - inspect JSONL keys/count against schema
3. PT35 Wi-Fi path:
   - start recording via UI
   - set label
   - verify status panel and frame growth
4. Recovery AP path:
   - leave normal WLAN
   - start rescue AP
   - verify collar reachability and control path
5. BLE fallback:
   - force BLE mode
   - send label/event
   - verify UI shows `SENT / UNCONFIRMED`
   - verify marker effect in collar logs/data
6. Desktop UX:
   - PetMind Training shortcut launches training UI
   - existing Collar Pet dashboard remains unchanged

## 9. Troubleshooting

If PT35 UI cannot control recorder:
- verify `~/.config/collarpet/link.conf`
- verify SSH key and host reachability
- verify `/usr/local/bin/cp-petmind-record` exists on collar
- use recovery AP path and retry

If BLE fallback appears to do nothing:
- remember fallback is one-way and unconfirmed
- verify collar-side logs/data for marker application

If model is unavailable at runtime:
- check model file path under `/home/jenna/collarpet/brain/`
- check import/runtime dependencies (`onnxruntime`, `numpy`)

## 10. Rollback / Recovery

For PT35 changes:
- keep backups before replacing launcher scripts/services
- for button daemon patches, use generated backup files such as:
  - `~/.local/bin/pt35-buttons.py.bak`
  - `~/.local/bin/pt35-buttons.py.before_numeric_fix`

For runtime changes:
- preserve previous runtime scripts and service configs before replacement
- restart only changed services first, then full reboot if device groups or udev access changed

## 11. Known Limitations / Future Work

- BLE fallback is transmit-only PT35 to collar.
- No BLE receipt acknowledgement.
- Context labels are not expression labels.
- Real-world data must be reviewed before training.
- Recovery AP must be tested before field use.
- Real-world data should augment synthetic data, not replace it blindly.
- Current real-world dataset is not yet a validated accuracy metric.

## Resource status in this repo

Present in tracked source:
- `pt35/training/cp-petmind-training`
- `pi/brain/petmind_nn_v0_3.py`
- `pi/brain/petmind_schema_v0_3.py`
- `pi/brain/petmind_v0_3.onnx`
- `pi/brain/petmind_v0_3.pt`
- `pi/scripts/update-collarpet-shared-ble-scanfix.sh`
- `pt35/scripts/add-petmind-training-desktop-shortcut.sh`
- `pt35/scripts/update-pt35-petmind-training-ui-v2.sh`
- `artifacts/CollarPet_PetMind_RealTraining_Prep_v1.zip`
