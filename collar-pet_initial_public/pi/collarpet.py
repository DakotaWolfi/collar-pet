#!/usr/bin/env python3
"""
CollarPet Linux Brain V9
========================

Pi/Linux responsibilities:
  - passive BLE environmental sensing + EF28 v2 decoding/tracking
  - deterministic safety/reflex layer + learned ONNX personality layer
  - I2S microphone capture, audio features, music detection + LED VU feed
  - single-owner queued ESP UART manager (RX + serialized/coalesced TX)
  - e-paper rendering
  - UART link to ESP32-S3 controller

ESP responsibilities:
  - WS2812 animation
  - DRV2605/LRA
  - BH1750
  - BMP280
  - BMI160
  - GPS
  - MAX30102 pulse/contact preprocessing
  - future buttons / stealth switch / local RGB LED

UART wiring:
  Pi TX GPIO14 / physical pin 8  -> ESP RX GPIO2
  Pi RX GPIO15 / physical pin 10 <- ESP TX GPIO1
  GND                            <-> GND

Pi UART:
  /dev/serial0, 115200 baud
"""

import asyncio
import signal
import struct
import time
import json
import csv
import logging
import sys
import math
import os
import subprocess
import socket
import multiprocessing as mp
import queue
from collections import deque
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
except Exception:
    np = None

from bleak import BleakScanner, BleakClient
import serial
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    from epaper_driver_select import EPD, DRIVER_NAME as EPAPER_DRIVER_NAME
    EPAPER_IMPORT_ERROR = None
except Exception as _epaper_import_error:
    EPD = None
    EPAPER_DRIVER_NAME = "unavailable"
    EPAPER_IMPORT_ERROR = _epaper_import_error


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VERSION = "9.4.2-remote-style-pi-ui"

# Known-song matching
SONG_DB_DIR = Path("/home/jenna/collarpet/songdb")
SONG_CATALOG_FILE = SONG_DB_DIR / "songs.csv"
SONG_FP_DIR = SONG_DB_DIR / "fingerprints"
SONG_INDEX_DIR = SONG_DB_DIR / "index"
SONG_INDEX_MANIFEST = SONG_INDEX_DIR / "manifest.json"
SONG_INDEX_SID_MAP = SONG_INDEX_DIR / "sid_map.json"
SONG_INDEX_HASHES = SONG_INDEX_DIR / "unique_hashes.u32"
SONG_INDEX_OFFSETS = SONG_INDEX_DIR / "offsets.u32"
SONG_INDEX_POSTINGS = SONG_INDEX_DIR / "postings.u32"
SONG_MATCH_INTERVAL = 2.0
SONG_WINDOW_SECONDS = 6.0
SONG_MIN_HASH_VOTES = 6
SONG_MIN_UNIQUE_HASHES = 5

# Reject silence/near-silence before fingerprint normalization. Without this,
# a zero-filled ALSA stream normalizes into a perfectly repeatable synthetic
# fingerprint and can "recognize" a song from literal silence.
SONG_AUDIO_GATE_DBFS = float(os.environ.get("COLLARPET_SONG_GATE_DBFS", "-60.0"))

# Real known-song hits on the current database are normally far above these.
# New songs need a strong acquire score; an already locked song may be held
# with a lower score so recognition does not flicker during brief weak sections.
SONG_ACQUIRE_MIN_VOTES = int(os.environ.get("COLLARPET_SONG_ACQUIRE_VOTES", "70"))
SONG_HOLD_MIN_VOTES = int(os.environ.get("COLLARPET_SONG_HOLD_VOTES", "40"))
SONG_SWITCH_RATIO = float(os.environ.get("COLLARPET_SONG_SWITCH_RATIO", "1.20"))
SONG_SWITCH_GAP = int(os.environ.get("COLLARPET_SONG_SWITCH_GAP", "25"))

SONG_ANNOUNCE_SECONDS = 2.0
SONG_REARM_SECONDS = 12.0

EF28_MFG_ID = 0x28EF
DISPLAY_PRIORITY = "BADGES"   # BADGES / BEACONS / STRONGEST / AUTO

# Tail/Ears UI state. The BLE gear manager will update the *_CONNECTED flags
# when that integration is enabled. Keeping these fields in the remote protocol
# now means the remotes do not need another UI/protocol redesign later.
GEAR_MAIN_ENABLED = os.environ.get("COLLARPET_GEAR_ENABLE", "1").lower() not in ("0", "false", "no", "off")
TAIL_ENABLED = os.environ.get("COLLARPET_TAIL_ENABLE", "1").lower() not in ("0", "false", "no", "off")
EARS_ENABLED = os.environ.get("COLLARPET_EARS_ENABLE", "1").lower() not in ("0", "false", "no", "off")
TAIL_ACTIVE_MODE = os.environ.get("COLLARPET_TAIL_ACTIVE", "0").lower() not in ("0", "false", "no", "off")
TAIL_WAG_KNOWN_SONG = os.environ.get("COLLARPET_TAIL_SONG_WAG", "0").lower() not in ("0", "false", "no", "off")
EARS_ACTIVE_MODE = os.environ.get("COLLARPET_EARS_ACTIVE", "0").lower() not in ("0", "false", "no", "off")
TAIL_CONNECTED = False
EARS_CONNECTED = False

# Tail Company MiTail BLE profiles.  Keep both generations because stock
# MiTail firmware in the wild may expose either the original MiTail UUIDs or
# the newer unified TailControl UUIDs.  Built-in motion/LED command names are
# common to both.
MITAIL_PROFILES = (
    {
        "name": "tailcontrol",
        "service": "19f8ade2-d0c6-4c0a-912a-30601d9b3060",
        "rx": "5e4d86ac-ef2f-466f-a857-8776d45ffbc2",
        "tx": "567a99d6-a442-4ac0-b676-4993bf95f805",
        "battery": "e818bda3-88a7-43c0-8509-6e0bbb6f55d9",
    },
    {
        "name": "mitail",
        "service": "3af2108b-d066-42da-a7d4-55648fa0a9b6",
        "rx": "5bfd6484-ddee-4723-bfe6-b653372bbfd6",
        "tx": "c6612b64-0087-4974-939e-68968ef294b0",
        "battery": "b08fed02-0584-40ef-b006-aff7e0d24e13",
    },
)

TAIL_MOVE_COMMANDS = {
    "TAILHM", "TAILS1", "TAILS2", "TAILS3", "TAILFA", "TAILSH",
    "TAILHA", "TAILER", "TAILEP", "TAILT1", "TAILT2", "TAILET",
}
TAIL_LED_COMMANDS = {
    "LEDOFF", "LEDREC", "LEDTRI", "LEDSAW", "LEDSOS", "LEDBEA",
    "LEDFLA", "LEDSTR",
}
TAIL_IDLE_DISCONNECT_SECONDS = float(os.environ.get("COLLARPET_TAIL_IDLE", "30"))

EARGEAR_PROFILES = (
    {"name":"tailcontrol","service":"19f8ade2-d0c6-4c0a-912a-30601d9b3060","rx":"5e4d86ac-ef2f-466f-a857-8776d45ffbc2","tx":"567a99d6-a442-4ac0-b676-4993bf95f805","battery":"e818bda3-88a7-43c0-8509-6e0bbb6f55d9"},
    {"name":"eargear2","service":"927dee04-ddd4-4582-8e42-69dc9fbfae66","rx":"05e026d8-b395-4416-9f8a-c00d6c3781b9","tx":"0b646a19-371e-4327-b169-9632d56c0e84","battery":"54fa919d-e8a8-4841-b280-c5461161304f"},
)
EAR_COMMANDS = {"LISTENMODE", "STOPLISTEN", "TILTMODE", "STOPTILT"}
EAR_IDLE_DISCONNECT_SECONDS = float(os.environ.get("COLLARPET_EARS_IDLE", "30"))

DEVICE_TIMEOUT = 8.0
RSSI_ALPHA = 0.20

ESP_PORT = os.environ.get("COLLARPET_ESP_PORT", "/dev/ttyS7")
ESP_BAUD = 115200

# Keep ESP alive and keep its RF device state current.
# Linux initiates HELLO immediately after opening UART, so the ESP can remain
# silent during Orange Pi boot. Unpaired retries are intentionally slow.
ESP_HELLO_INTERVAL = 30.0
ESP_PING_INTERVAL = 2.0
ESP_WATCHDOG_SECONDS = 10.0

# systemd application watchdog. The service unit uses WatchdogSec=20; this task
# feeds it every 5 seconds. If the Python event loop wedges, systemd restarts us.
SYSTEMD_WATCHDOG_FEED_SECONDS = 5.0

ESP_DEVICE_SYNC_INTERVAL = 0.75

# I2S microphone / ALSA capture.
# The runtime deliberately uses arecord instead of a Python audio package so it
# works on a minimal Raspberry Pi install once ALSA exposes the I2S capture PCM.
# Override the device without editing the file:
#   COLLARPET_AUDIO_DEVICE=plughw:0,0
AUDIO_DEVICE = os.environ.get("COLLARPET_AUDIO_DEVICE", "hw:0,0")
AUDIO_RATE = int(os.environ.get("COLLARPET_AUDIO_RATE", "48000"))
AUDIO_CHANNELS = int(os.environ.get("COLLARPET_AUDIO_CHANNELS", "2"))
AUDIO_FORMAT = "S32_LE"
AUDIO_CHUNK_FRAMES = int(os.environ.get("COLLARPET_AUDIO_CHUNK_FRAMES", "1536"))
AUDIO_MUSIC_ENTER_CONF = 0.52
AUDIO_MUSIC_EXIT_CONF = 0.30
AUDIO_MUSIC_ENTER_SECONDS = 0.8
AUDIO_MUSIC_EXIT_SECONDS = 2.0
AUDIO_VU_INTERVAL = 0.040       # 25 Hz UART level stream while music is active
AUDIO_STALE_SECONDS = 1.0

# E-paper
# E-paper responsiveness.
# Mood/status changes use fast partial refreshes; a slower full refresh is only
# used periodically to clean accumulated ghosting.
FULL_REFRESH_MIN_INTERVAL = 45.0
PARTIAL_REFRESH_INTERVAL = 1.25
MAX_PARTIAL_REFRESHES = 18
DISPLAY_LOOP_INTERVAL = 0.20
EPAPER_ENABLED = os.environ.get("COLLARPET_EPAPER", "auto").strip().lower()
if EPAPER_ENABLED == "auto":
    EPAPER_ENABLED = EPD is not None
else:
    EPAPER_ENABLED = EPAPER_ENABLED not in ("0", "false", "no", "off")
EPAPER_AVAILABLE = bool(EPAPER_ENABLED and EPD is not None)

ASSET_DIR = Path("/home/jenna/collarpet/assets/wolves")
SHUTDOWN_WOLF = ASSET_DIR / "wolf_shutdown.png"
REBOOT_WOLF = ASSET_DIR / "wolf_reboot.png"
WOLF_FILES = {
    "idle": ASSET_DIR / "wolf_idle.png",
    "curious": ASSET_DIR / "wolf_curious.png",
    "happy": ASSET_DIR / "wolf_happy.png",
    "annoyed": ASSET_DIR / "wolf_annoyed.png",
    "foxfound": ASSET_DIR / "wolf_foxfound.png",
    "sleep": ASSET_DIR / "wolf_sleep.png",
    "listening": ASSET_DIR / "wolf_listening.png",
    "tracking": ASSET_DIR / "wolf_tracking.png",
    "startled": ASSET_DIR / "wolf_startled.png",
    "overwhelmed": ASSET_DIR / "wolf_overwhelmed.png",
    "social": ASSET_DIR / "wolf_social.png",
    "smug": ASSET_DIR / "wolf_smug.png",
}

# Environment / fusion thresholds
DARK_LUX = 10.0
DIM_LUX = 80.0
BRIGHT_LUX = 2000.0
GPS_STALE_SECONDS = 4.0
GPS_STATIONARY_KMH = 3.0
GPS_GOOD_HDOP = 2.5
GPS_MIN_SATS = 6


# Persistent runtime data
BASE_DIR = Path("/home/jenna/collarpet")
LOG_DIR = BASE_DIR / "logs"
STATE_DIR = BASE_DIR / "state"
LOG_FILE = LOG_DIR / "collarpet.log"
EVENT_FILE = LOG_DIR / "events.jsonl"
MEMORY_FILE = STATE_DIR / "pet_memory.json"
SETTINGS_FILE = STATE_DIR / "settings.json"
STATUS_FILE = STATE_DIR / "status.json"
GEAR_FILE = STATE_DIR / "gear.json"

# Neural PetMind.  The model is deliberately advisory: deterministic safety
# rules remain authoritative.
BRAIN_DIR = BASE_DIR / "brain"
NEURAL_MODEL_FILE = BRAIN_DIR / "petmind_v0_1.onnx"
NEURAL_INTERVAL = 0.50
NEURAL_MIN_CONFIDENCE = 0.28
EXPRESSION_HOLD_SECONDS = 4.0

# Ordinary BLE is treated as anonymous environmental texture. We do not
# persist ordinary BLE addresses or try to identify people from them.
BLE_ENV_TIMEOUT = 20.0
BLE_NEW_WINDOW = 15.0
BLE_STRONG_RSSI = -60

# Heltec Vision Master E213 external mirror.  The existing passive BLE scanner
# discovers it; a separate task then connects and mirrors the live V8 state.
REMOTE_DEVICE_NAME = "CollarPet-Remote"
REMOTE_SERVICE_UUID = "8b94c600-3d23-4f0d-8c58-cf346f9b7d10"
REMOTE_DISPLAY_UUID = "8b94c601-3d23-4f0d-8c58-cf346f9b7d10"
REMOTE_COMMAND_UUID = "8b94c602-3d23-4f0d-8c58-cf346f9b7d10"
REMOTE_RECONNECT_DELAY = 2.0
REMOTE_MIN_SEND_INTERVAL = 2.0
REMOTE_FORCE_SEND_INTERVAL = 15.0
REMOTE_DISCOVERY_TIMEOUT = 12.0


sys.path.insert(0, str(BRAIN_DIR))
try:
    from petmind_nn_v0_1 import NeuralPetMind
except Exception as _brain_import_error:
    NeuralPetMind = None



# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class EFDevice:
    address: str
    name: str = ""
    dev_type: str = "?"
    dev_id: int = 0
    flags: int = 0
    tx_power: int = 0
    rssi: float = -100.0
    raw_rssi: int = -100
    last_seen: float = 0.0
    raw_payload: bytes = b""

    @property
    def is_badge(self):
        return self.dev_type == "D"

    @property
    def is_beacon(self):
        return self.dev_type == "B"


@dataclass
class GPSState:
    fix: bool = False
    lat: Optional[float] = None
    lon: Optional[float] = None
    speed_kmh: float = 0.0
    satellites: int = 0
    hdop: Optional[float] = None
    altitude_m: Optional[float] = None
    last_update: float = 0.0

    @property
    def confidence(self):
        if not self.fix:
            return "NONE"
        if (
            self.satellites >= GPS_MIN_SATS
            and self.hdop is not None
            and self.hdop <= GPS_GOOD_HDOP
        ):
            return "GOOD"
        return "LOW"


@dataclass
class PulseState:
    present: bool = False
    contact: bool = False
    bpm: Optional[float] = None
    quality: float = 0.0
    ir: int = 0
    red: int = 0
    last_update: float = 0.0

    @property
    def valid(self):
        return (
            self.present
            and self.contact
            and self.bpm is not None
            and self.bpm > 0
            and self.quality >= 60.0
        )


@dataclass
class ESPState:
    connected: bool = False
    handshake: bool = False
    ready_version: str = ""
    last_rx: float = 0.0
    last_pong: float = 0.0
    failsafe: bool = True
    stealth: bool = False

    light_lux: Optional[float] = None
    temp_c: Optional[float] = None
    pressure_hpa: Optional[float] = None

    accel_g: Tuple[float, float, float] = (0.0, 0.0, 1.0)
    gyro_dps: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    motion_score: float = 0.0
    moving: bool = False
    motion_class: str = "NORMAL"
    last_gesture: str = ""
    last_gesture_time: float = 0.0

    gps: GPSState = field(default_factory=GPSState)
    pulse: PulseState = field(default_factory=PulseState)


@dataclass
class WorldState:
    mood: str = "idle"
    activity: str = "STILL"
    environment: str = "UNKNOWN"
    light_desc: str = "LIGHT ?"
    gps_desc: str = "GPS ?"
    reason: str = "booting"


@dataclass
class BLEEnvDevice:
    first_seen: float
    last_seen: float
    rssi: float


class BLEEnvironment:
    """Anonymous, short-lived BLE crowd/environment statistics."""
    def __init__(self):
        self.devices: Dict[str, BLEEnvDevice] = {}
        self.last_density = 0
        self.last_density_time = time.monotonic()
        self.change = 0.0

    def observe(self, address: str, rssi: float):
        now = time.monotonic()
        old = self.devices.get(address)
        if old is None:
            self.devices[address] = BLEEnvDevice(now, now, float(rssi))
        else:
            old.last_seen = now
            old.rssi = RSSI_ALPHA * float(rssi) + (1.0 - RSSI_ALPHA) * old.rssi

    def stats(self, now=None):
        if now is None:
            now = time.monotonic()
        stale = [a for a, d in self.devices.items() if now - d.last_seen > BLE_ENV_TIMEOUT]
        for a in stale:
            self.devices.pop(a, None)

        active = list(self.devices.values())
        density = len(active)
        new_count = sum(1 for d in active if now - d.first_seen <= BLE_NEW_WINDOW)
        strong_count = sum(1 for d in active if d.rssi >= BLE_STRONG_RSSI)
        new_ratio = (new_count / density) if density else 0.0
        strong_ratio = (strong_count / density) if density else 0.0

        if now - self.last_density_time >= 5.0:
            delta = density - self.last_density
            self.change = max(-1.0, min(1.0, delta / 20.0))
            self.last_density = density
            self.last_density_time = now

        return {
            "density": density,
            "new_ratio": new_ratio,
            "strong_ratio": strong_ratio,
            "change": self.change,
        }




def _song_fingerprint(samples, sample_rate):
    """Fingerprint mono float audio. Must match collarpet_songprep_v2.py."""
    if np is None or samples is None or len(samples) < 4096:
        return [], []

    target_sr = 11025
    nfft = 2048
    hop = 512
    freq_radius = 10
    floor_db = -55.0
    fanout = 8
    min_dt = 1
    max_dt = 90

    x = np.asarray(samples, dtype=np.float32)

    # IMPORTANT: gate on the original absolute amplitude before normalization.
    # This prevents digital silence / tiny capture noise from being amplified
    # into a full-scale spectral pattern.
    input_rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if x.size else 0.0
    input_dbfs = 20.0 * math.log10(max(input_rms, 1e-9))
    if input_dbfs < SONG_AUDIO_GATE_DBFS:
        return [], []

    if sample_rate != target_sr:
        duration = len(x) / float(sample_rate)
        n = max(1, int(round(duration * target_sr)))
        old = np.linspace(0.0, 1.0, len(x), endpoint=False)
        new = np.linspace(0.0, 1.0, n, endpoint=False)
        x = np.interp(new, old, x).astype(np.float32)

    mx = float(np.max(np.abs(x))) if x.size else 0.0
    if mx > 0:
        x = x / mx

    n_frames = 1 + max(0, (len(x) - nfft) // hop)
    if n_frames <= 0:
        return [], []

    window = np.hanning(nfft).astype(np.float32)
    peaks = []
    for t in range(n_frames):
        frame = x[t*hop:t*hop+nfft]
        if len(frame) < nfft:
            frame = np.pad(frame, (0, nfft-len(frame)))
        spec = np.abs(np.fft.rfft(frame * window)) + 1e-10
        db = 20.0 * np.log10(spec)
        db -= float(np.max(db))
        cand = []
        for f in range(freq_radius, len(db)-freq_radius):
            v = float(db[f])
            if v < floor_db:
                continue
            if v >= float(np.max(db[f-freq_radius:f+freq_radius+1])):
                cand.append((v, f))
        cand.sort(reverse=True)
        for a, f in cand[:6]:
            peaks.append((t, f))

    hashes, times = [], []
    for i, (t1, f1) in enumerate(peaks):
        used = 0
        for j in range(i+1, len(peaks)):
            t2, f2 = peaks[j]
            dt = t2 - t1
            if dt < min_dt:
                continue
            if dt > max_dt:
                break
            hashes.append(((f1 & 0x7FF) << 21) | ((f2 & 0x7FF) << 10) | (dt & 0x3FF))
            times.append(t1)
            used += 1
            if used >= fanout:
                break
    return hashes, times


class SongFingerprintDB:
    """CollarPet SongDB v3 compact mmap index."""
    SID_BITS = 10
    TIME_BITS = 22
    TIME_MASK = (1 << TIME_BITS) - 1

    def __init__(self):
        self.songs = []
        self.unique_hashes = None
        self.offsets = None
        self.postings = None
        self.unique_hash_count = 0
        self.posting_count = 0
        self.loaded = False

    @staticmethod
    def _enabled(value):
        return str(value).strip().lower() not in ("", "0", "false", "no", "off")

    def load(self):
        self.songs = []
        self.unique_hashes = None
        self.offsets = None
        self.postings = None
        self.unique_hash_count = 0
        self.posting_count = 0

        required = [
            SONG_CATALOG_FILE, SONG_INDEX_MANIFEST, SONG_INDEX_SID_MAP,
            SONG_INDEX_HASHES, SONG_INDEX_OFFSETS, SONG_INDEX_POSTINGS,
        ]
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            self.loaded = True
            print("[SONGDB] compact index missing:")
            for p in missing:
                print(f"[SONGDB]   {p}")
            return

        try:
            manifest = json.loads(SONG_INDEX_MANIFEST.read_text(encoding="utf-8"))
            if manifest.get("format") != "collarpet-songdb-compact-v1":
                raise RuntimeError(f"unsupported compact index format: {manifest.get('format')!r}")

            sid_map = json.loads(SONG_INDEX_SID_MAP.read_text(encoding="utf-8"))
            unique_count = int(manifest["unique_hash_count"])
            posting_count = int(manifest["occurrence_count"])

            if int(manifest.get("sid_bits", self.SID_BITS)) != self.SID_BITS:
                raise RuntimeError("compact index SID bit width mismatch")
            if int(manifest.get("time_bits", self.TIME_BITS)) != self.TIME_BITS:
                raise RuntimeError("compact index time bit width mismatch")
            if len(sid_map) != int(manifest["song_count"]):
                raise RuntimeError("sid_map song count does not match manifest")

            with SONG_CATALOG_FILE.open("r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            by_uuid = {str(r.get("uuid", "")).strip(): r for r in rows if str(r.get("uuid", "")).strip()}

            for uid in sid_map:
                row = by_uuid.get(str(uid), {})
                title = str(row.get("title", "")).strip() or f"[missing metadata {uid}]"
                artist = str(row.get("artist", "")).strip()
                album = str(row.get("album", "")).strip()
                display = f"{artist} - {title}" if artist else title
                self.songs.append({
                    "uuid": str(uid),
                    "artist": artist,
                    "title": title,
                    "album": album,
                    "display": display,
                    "enabled": self._enabled(row.get("enabled", "1")) if row else False,
                })

            # Raw little-endian uint32 arrays, backed directly by disk pages.
            self.unique_hashes = np.memmap(
                SONG_INDEX_HASHES, dtype="<u4", mode="r", shape=(unique_count,)
            )
            self.offsets = np.memmap(
                SONG_INDEX_OFFSETS, dtype="<u4", mode="r", shape=(unique_count + 1,)
            )
            self.postings = np.memmap(
                SONG_INDEX_POSTINGS, dtype="<u4", mode="r", shape=(posting_count,)
            )

            # Cheap structural validation without touching the whole index.
            if unique_count:
                if int(self.offsets[0]) != 0:
                    raise RuntimeError("compact index first offset is not zero")
                if int(self.offsets[-1]) != posting_count:
                    raise RuntimeError("compact index sentinel offset mismatch")
                if unique_count > 1 and int(self.unique_hashes[0]) > int(self.unique_hashes[-1]):
                    raise RuntimeError("compact hash index appears unsorted")

            self.unique_hash_count = unique_count
            self.posting_count = posting_count
            self.loaded = True
            enabled_count = sum(1 for s in self.songs if s["enabled"])
            print(
                f"[SONGDB] compact v1 mmap loaded {enabled_count}/{len(self.songs)} enabled song(s), "
                f"{unique_count:,} unique hashes, {posting_count:,} postings"
            )
        except Exception as e:
            self.loaded = True
            self.unique_hashes = self.offsets = self.postings = None
            LOGGER.error("compact song DB load failed: %s", e)
            print(f"[SONGDB] compact index load failed: {e}")

    def match(self, hashes, times):
        """Bounded-RAM matcher with diagnostics for acoustic live matching."""
        self.last_diag = {}
        if not self.loaded:
            self.load()
        if not self.songs or self.unique_hashes is None:
            self.last_diag = {"reason": "db_unavailable"}
            return None

        qh = np.asarray(hashes, dtype=np.uint32)
        qt = np.asarray(times, dtype=np.int64)
        if qh.size == 0:
            self.last_diag = {"reason": "no_query_hashes"}
            return None

        max_postings_per_hash = int(os.environ.get("COLLARPET_SONG_MAX_POSTINGS_PER_HASH", "768"))
        max_query_landmarks = int(os.environ.get("COLLARPET_SONG_MAX_QUERY_LANDMARKS", "320"))
        shortlist_size = int(os.environ.get("COLLARPET_SONG_SHORTLIST", "12"))

        pos = np.searchsorted(self.unique_hashes, qh, side="left")
        n_unique = self.unique_hash_count
        useful = []
        skipped_common = 0
        missing = 0
        present = 0

        for ih_u32, qtime, p in zip(qh, qt, pos):
            p = int(p)
            if p >= n_unique or int(self.unique_hashes[p]) != int(ih_u32):
                missing += 1
                continue
            present += 1
            lo = int(self.offsets[p])
            hi = int(self.offsets[p + 1])
            count = hi - lo
            if count <= 0:
                continue
            if count > max_postings_per_hash:
                skipped_common += 1
                continue
            useful.append((count, int(ih_u32), int(qtime), lo, hi))

        self.last_diag = {
            "query_hashes": int(qh.size),
            "present_hashes": int(present),
            "missing_hashes": int(missing),
            "skipped_common": int(skipped_common),
            "useful_before_limit": int(len(useful)),
        }

        if not useful:
            self.last_diag["reason"] = "no_useful_hashes"
            return None

        useful.sort(key=lambda x: x[0])
        if len(useful) > max_query_landmarks:
            useful = useful[:max_query_landmarks]
        self.last_diag["used_landmarks"] = int(len(useful))

        votes = {}
        for _count, _ih, qtime, lo, hi in useful:
            for packed in self.postings[lo:hi]:
                pv = int(packed)
                sid = pv >> self.TIME_BITS
                if sid >= len(self.songs) or not self.songs[sid]["enabled"]:
                    continue
                rt = pv & self.TIME_MASK
                key = (sid, rt - qtime)
                votes[key] = votes.get(key, 0) + 1

        if not votes:
            self.last_diag["reason"] = "no_votes"
            return None

        ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
        candidate_keys = [key for key, _ in ranked[:max(2, shortlist_size)]]
        support = {key: set() for key in candidate_keys}
        wanted = set(candidate_keys)

        for _count, ih, qtime, lo, hi in useful:
            for packed in self.postings[lo:hi]:
                pv = int(packed)
                sid = pv >> self.TIME_BITS
                if sid >= len(self.songs) or not self.songs[sid]["enabled"]:
                    continue
                rt = pv & self.TIME_MASK
                key = (sid, rt - qtime)
                if key in wanted:
                    support[key].add(ih)

        top_key, top_votes = ranked[0]
        top_sid, top_offset = top_key
        top_unique = len(support.get(top_key, ()))
        top_song = self.songs[top_sid]
        self.last_diag.update({
            "reason": "below_threshold",
            "best_display": top_song["display"],
            "best_uuid": top_song["uuid"],
            "best_votes": int(top_votes),
            "best_unique": int(top_unique),
            "best_offset": int(top_offset),
        })

        best_key = None
        best = 0
        uniq = 0
        for key, vote_count in ranked[:max(2, shortlist_size)]:
            u = len(support.get(key, ()))
            if vote_count >= SONG_MIN_HASH_VOTES and u >= SONG_MIN_UNIQUE_HASHES:
                best_key = key
                best = int(vote_count)
                uniq = int(u)
                break

        if best_key is None:
            return None

        sid, offset = best_key
        winner = self.songs[sid]

        runner_votes = 0
        runner_title = ""
        for (rsid, _roffset), rvotes in ranked:
            if rsid != sid:
                runner_votes = int(rvotes)
                runner_title = self.songs[rsid]["display"]
                break

        gap = best - runner_votes
        ratio = (float(best) / max(1.0, float(runner_votes))) if runner_votes else 999.0
        vote_conf = min(1.0, max(0.0, (best - SONG_HOLD_MIN_VOTES) / 900.0))
        margin_conf = min(1.0, max(0.0, (ratio - 1.0) / 1.5))
        conf = min(0.99, 0.50 + 0.32 * vote_conf + 0.17 * margin_conf)

        self.last_diag.update({
            "reason": "match",
            "best_display": winner["display"],
            "best_uuid": winner["uuid"],
            "best_votes": best,
            "best_unique": uniq,
            "runner_votes": runner_votes,
            "gap": gap,
            "ratio": ratio,
        })

        return {
            "uuid": winner["uuid"],
            "artist": winner["artist"],
            "title": winner["title"],
            "album": winner["album"],
            "display": winner["display"],
            "votes": best,
            "unique": uniq,
            "confidence": conf,
            "runner_votes": runner_votes,
            "runner_title": runner_title,
            "gap": gap,
            "ratio": ratio,
            "used_landmarks": len(useful),
            "skipped_common": skipped_common,
            "missing_hashes": missing,
        }




# Song recognition is isolated in another Linux process.
# Main CollarPet stays responsive; if matching falls behind, work is dropped
# instead of blocking UART/BLE/audio.
_song_audio_chunks = deque()
_song_audio_frames = 0
_song_last_submit = 0.0
_song_job_seq = 0
_song_worker_process = None
_song_worker_in = None
_song_worker_out = None
_song_announce_artist = ""
_song_announce_title = ""
_song_announce_confidence = 0.0
_song_announce_until = 0.0
_song_episode_uuid = ""


def _song_worker_main(in_q, out_q):
    """CPU-heavy fingerprint worker. Runs in its own process/core."""
    db = SongFingerprintDB()
    db.load()
    try:
        out_q.put_nowait({"kind": "ready", "songs": len(db.songs), "hashes": db.unique_hash_count})
    except Exception:
        pass

    while True:
        job = in_q.get()
        if job is None:
            return

        seq, chunks, sample_rate = job
        started = time.monotonic()
        try:
            window = np.concatenate(chunks).astype(np.float32, copy=False) if chunks else np.empty(0, dtype=np.float32)
            hs, ts = _song_fingerprint(window, sample_rate)
            result = db.match(hs, ts)
            payload = {
                "kind": "result",
                "seq": seq,
                "match": result,
                "diag": getattr(db, "last_diag", {}),
                "query_hashes": len(hs),
                "window_seconds": (len(window) / float(sample_rate)) if sample_rate else 0.0,
                "elapsed": time.monotonic() - started,
            }
        except Exception as e:
            payload = {"kind": "error", "seq": seq, "error": str(e)}

        try:
            out_q.put_nowait(payload)
        except queue.Full:
            try:
                out_q.get_nowait()
            except Exception:
                pass
            try:
                out_q.put_nowait(payload)
            except Exception:
                pass


def _song_worker_start():
    global _song_worker_process, _song_worker_in, _song_worker_out
    if _song_worker_process is not None and _song_worker_process.is_alive():
        return

    ctx = mp.get_context("fork")
    _song_worker_in = ctx.Queue(maxsize=1)
    _song_worker_out = ctx.Queue(maxsize=4)
    _song_worker_process = ctx.Process(
        target=_song_worker_main,
        args=(_song_worker_in, _song_worker_out),
        name="collarpet-songmatch",
        daemon=True,
    )
    _song_worker_process.start()
    print(f"[SONGWORKER] started pid={_song_worker_process.pid}")


def _song_worker_stop():
    global _song_worker_process
    if _song_worker_process is None:
        return
    try:
        _song_worker_in.put_nowait(None)
    except Exception:
        pass
    _song_worker_process.join(timeout=2.0)
    if _song_worker_process.is_alive():
        _song_worker_process.terminate()
        _song_worker_process.join(timeout=1.0)
    print("[SONGWORKER] stopped")
    _song_worker_process = None


def _song_buffer_clear():
    global _song_audio_frames
    _song_audio_chunks.clear()
    _song_audio_frames = 0


def _song_buffer_add(samples):
    """Keep ~SONG_WINDOW_SECONDS using numpy chunks, not Python floats."""
    global _song_audio_frames
    chunk = np.asarray(samples, dtype=np.float32).copy()
    _song_audio_chunks.append(chunk)
    _song_audio_frames += int(chunk.size)
    max_frames = int(AUDIO_RATE * SONG_WINDOW_SECONDS)

    while _song_audio_chunks and _song_audio_frames - _song_audio_chunks[0].size >= max_frames:
        old = _song_audio_chunks.popleft()
        _song_audio_frames -= int(old.size)


async def song_match_task():
    """Submit work and consume SongDB-v3 results without blocking the event loop."""
    global _song_last_submit, _song_job_seq
    global _song_announce_artist, _song_announce_title, _song_announce_confidence, _song_announce_until
    global _song_episode_uuid

    while not stop_event.is_set():
        now = time.monotonic()

        if _song_worker_out is not None:
            while True:
                try:
                    msg = _song_worker_out.get_nowait()
                except queue.Empty:
                    break
                except Exception as e:
                    LOGGER.debug("song worker result queue error: %s", e)
                    break

                kind = msg.get("kind")
                if kind == "ready":
                    print(f'[SONGDB] worker loaded {msg.get("songs", 0)} song(s), {msg.get("hashes", 0)} unique hashes')
                elif kind == "error":
                    LOGGER.warning("song worker error: %s", msg.get("error", "?"))
                elif kind == "result":
                    match = msg.get("match")
                    elapsed = float(msg.get("elapsed", 0.0))
                    query_hashes = int(msg.get("query_hashes", 0))
                    window_seconds = float(msg.get("window_seconds", 0.0))
                    diag = msg.get("diag") or {}

                    if match:
                        audio_fresh = audio.available and audio.last_update and (now - audio.last_update <= AUDIO_STALE_SECONDS)
                        audible = audio.dbfs >= SONG_AUDIO_GATE_DBFS
                        same_locked_song = (
                            audio.song_uuid == match["uuid"]
                            and audio.song_last_seen
                            and now - audio.song_last_seen <= SONG_REARM_SECONDS
                        )
                        min_votes = SONG_HOLD_MIN_VOTES if same_locked_song else SONG_ACQUIRE_MIN_VOTES
                        runner_ok = (
                            match.get("runner_votes", 0) == 0
                            or (
                                match.get("ratio", 0.0) >= SONG_SWITCH_RATIO
                                and match.get("gap", 0) >= SONG_SWITCH_GAP
                            )
                        )
                        accepted = audio_fresh and audible and match["votes"] >= min_votes and runner_ok

                        if accepted:
                            previous_seen = audio.song_last_seen
                            previous_episode = _song_episode_uuid

                            audio.song_uuid = match["uuid"]
                            audio.song_artist = match.get("artist", "")
                            audio.song_title = match["title"]
                            audio.song_confidence = match["confidence"]
                            audio.song_last_seen = now
                            audio.music_confidence = max(
                                audio.music_confidence,
                                min(0.98, 0.78 + 0.20 * audio.song_confidence),
                            )

                            new_episode = (
                                match["uuid"] != previous_episode
                                or not previous_seen
                                or now - previous_seen > SONG_REARM_SECONDS
                            )
                            _song_episode_uuid = match["uuid"]

                            if new_episode:
                                _song_announce_artist = match.get("artist", "")
                                _song_announce_title = match["title"]
                                _song_announce_confidence = match["confidence"]
                                _song_announce_until = now + SONG_ANNOUNCE_SECONDS
                                if GEAR_MAIN_ENABLED and TAIL_ENABLED and TAIL_WAG_KNOWN_SONG:
                                    try: gear_manager.submit("MOVE", "TAILHA")
                                    except Exception: pass
                                force_epaper_refresh.set()
                                print(
                                    f'[NOWPLAYING] {match["display"]} '
                                    f'{match["confidence"]*100:.0f}% announce={SONG_ANNOUNCE_SECONDS:.0f}s'
                                )

                            print(
                                f'[SONG] {match["display"]} match={audio.song_confidence*100:.0f}% '
                                f'votes={match["votes"]} runner={match.get("runner_votes", 0)} '
                                f'gap={match.get("gap", 0)} worker={elapsed:.2f}s'
                            )
                        elif match["votes"] >= SONG_HOLD_MIN_VOTES:
                            print(
                                f'[SONG?] reject {match["display"]} votes={match["votes"]} '
                                f'runner={match.get("runner_votes", 0)} gap={match.get("gap", 0)} '
                                f'ratio={match.get("ratio", 0.0):.2f} '
                                f'audio={audio.dbfs:.1f}dBFS fresh={audio_fresh}'
                            )
                    else:
                        # Do not stay mysteriously silent while debugging SongDB.
                        # This is only one line per worker result (~2 s cadence).
                        print(
                            f"[SONGSCAN] no match hashes={query_hashes} "
                            f"present={diag.get('present_hashes', 0)} "
                            f"useful={diag.get('used_landmarks', diag.get('useful_before_limit', 0))} "
                            f"common={diag.get('skipped_common', 0)} "
                            f"best={diag.get('best_votes', 0)}/{diag.get('best_unique', 0)} "
                            f"candidate={diag.get('best_display', '-')} "
                            f"window={window_seconds:.1f}s audio={audio.dbfs:.1f}dBFS "
                            f"worker={elapsed:.2f}s"
                        )

                    if not match and audio.song_last_seen and now - audio.song_last_seen > 8.0:
                        audio.song_uuid = ""
                        audio.song_artist = ""
                        audio.song_title = ""
                        audio.song_confidence = 0.0
                        if now - audio.song_last_seen > SONG_REARM_SECONDS:
                            _song_episode_uuid = ""

        audio_fresh = audio.available and audio.last_update and (now - audio.last_update <= AUDIO_STALE_SECONDS)
        audible = audio.dbfs >= SONG_AUDIO_GATE_DBFS
        if not audio_fresh or not audible:
            _song_buffer_clear()
            if audio.song_last_seen and now - audio.song_last_seen > 2.0:
                audio.song_uuid = ""
                audio.song_artist = ""
                audio.song_title = ""
                audio.song_confidence = 0.0

        enough = _song_audio_frames >= int(AUDIO_RATE * max(5.0, SONG_WINDOW_SECONDS - 0.5))
        if audio_fresh and audible and enough and now - _song_last_submit >= SONG_MATCH_INTERVAL and _song_worker_in is not None:
            _song_last_submit = now
            _song_job_seq += 1
            try:
                _song_worker_in.put_nowait((_song_job_seq, list(_song_audio_chunks), AUDIO_RATE))
            except queue.Full:
                pass
            except Exception as e:
                LOGGER.debug("song worker submit error: %s", e)

        await asyncio.sleep(0.10)


@dataclass
class AudioState:
    available: bool = False
    device: str = ""
    rms: float = 0.0
    peak: float = 0.0
    dbfs: float = -120.0
    level: float = 0.0
    peak_level: float = 0.0
    music_confidence: float = 0.0
    speech_confidence: float = 0.0
    rhythmicity: float = 0.0
    spectral_flatness: float = 1.0
    spectral_flux: float = 0.0
    music: bool = False
    sudden: bool = False
    song_uuid: str = ""
    song_artist: str = ""
    song_title: str = ""
    song_confidence: float = 0.0
    song_last_seen: float = 0.0
    last_update: float = 0.0
    last_error: str = ""


@dataclass
class NeuralState:
    available: bool = False
    expression: str = "idle"
    confidence: float = 0.0
    top: List[Tuple[str, float]] = field(default_factory=list)
    last_infer: float = 0.0
    last_error: str = ""
    held_expression: str = "idle"
    held_since: float = 0.0


@dataclass
class RemoteMirrorState:
    discovered: bool = False
    connected: bool = False
    address: str = ""
    rssi: int = -100
    last_seen: float = 0.0
    last_send: float = 0.0
    last_payload: Optional[Tuple] = None
    error: str = ""


class PetMind:
    """
    First persistent 'mind' layer.

    This is deliberately small and understandable:
      curiosity: desire to investigate / seek stimulus
      arousal:   immediate excitement / activity level
      boredom:   rises during long quiet periods
      familiarity: remembers EF devices seen before

    Later a learned model can consume these values rather than replacing them.
    """

    def __init__(self):
        self.curiosity = 0.30
        self.arousal = 0.15
        self.boredom = 0.0
        self.last_update = time.monotonic()
        self.last_stimulus = time.monotonic()
        self.last_lux = None
        self.seen_devices = {}
        self.new_device_pulse = 0.0
        self.gesture_pulse = 0.0
        self.gesture_reason = ""
        self.pulse_baseline = 80.0
        self.load()

    def load(self):
        try:
            data = json.loads(MEMORY_FILE.read_text())
            self.curiosity = float(data.get("curiosity", self.curiosity))
            self.seen_devices = dict(data.get("seen_devices", {}))
            self.pulse_baseline = float(data.get("pulse_baseline", self.pulse_baseline))
        except Exception:
            pass

    def save(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = MEMORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "version": 1,
            "curiosity": round(self.curiosity, 4),
            "pulse_baseline": round(self.pulse_baseline, 2),
            "seen_devices": self.seen_devices,
            "saved": datetime.now().isoformat(timespec="seconds"),
        }, indent=2))
        tmp.replace(MEMORY_FILE)

    def observe_device(self, dev_id: int, dev_type: str, name: str):
        key = f"{dev_id:08X}"
        now_wall = datetime.now().isoformat(timespec="seconds")
        known = key in self.seen_devices

        entry = self.seen_devices.setdefault(key, {
            "type": dev_type,
            "name": name,
            "first_seen": now_wall,
            "count": 0,
        })
        entry["last_seen"] = now_wall
        entry["count"] = int(entry.get("count", 0)) + 1
        if name:
            entry["name"] = name

        if not known:
            self.new_device_pulse = min(1.0, self.new_device_pulse + 0.75)
            self.curiosity = min(1.0, self.curiosity + 0.22)
            self.arousal = min(1.0, self.arousal + 0.25)
            self.last_stimulus = time.monotonic()
            log_event("new_device", id=key, type=dev_type, name=name)

        return not known

    def observe_gesture(self, gesture: str):
        gesture = gesture.upper()
        self.last_stimulus = time.monotonic()

        if gesture == "SHAKE":
            self.gesture_pulse = 1.0
            self.gesture_reason = "head shake?"
            self.curiosity = min(1.0, self.curiosity + 0.10)
            self.arousal = min(1.0, self.arousal + 0.08)
        elif gesture == "FIDGET":
            self.gesture_pulse = 0.70
            self.gesture_reason = "scratch/fidget?"
            self.curiosity = min(1.0, self.curiosity + 0.07)
            self.arousal = min(1.0, self.arousal + 0.05)
        elif gesture == "JOLT":
            self.gesture_pulse = 0.85
            self.gesture_reason = "sudden movement"
            self.curiosity = min(1.0, self.curiosity + 0.09)
            self.arousal = min(1.0, self.arousal + 0.10)

        log_event("gesture", gesture=gesture)

    def update(self, visible_count: int, motion_class: str, motion_score: float, lux):
        now = time.monotonic()
        dt = max(0.0, min(2.0, now - self.last_update))
        self.last_update = now

        quiet_for = now - self.last_stimulus

        motion_class = (motion_class or "NORMAL").upper()

        # NORMAL includes ordinary collar motion up to roughly score 2.
        # That means breathing, looking around, small posture shifts, etc.
        # do not keep the pet permanently excited.
        interesting_motion = motion_class in ("STRONG", "SUDDEN")

        if visible_count == 0 and not interesting_motion:
            self.boredom = min(1.0, self.boredom + 0.010 * dt)
        else:
            self.boredom = max(0.0, self.boredom - 0.08 * dt)

        # A bored wolf gets increasingly curious even without external input.
        self.curiosity += self.boredom * 0.006 * dt

        if motion_class == "NORMAL":
            self.arousal = max(0.05, self.arousal - 0.030 * dt)

        elif motion_class == "NOTICEABLE":
            # Deliberately almost neutral: wearer is simply doing things.
            self.arousal = max(0.05, self.arousal - 0.010 * dt)

        elif motion_class == "STRONG":
            self.arousal = min(1.0, self.arousal + 0.035 * dt)
            self.curiosity = min(1.0, self.curiosity + 0.010 * dt)
            self.last_stimulus = now

        elif motion_class == "SUDDEN":
            self.arousal = min(1.0, self.arousal + 0.075 * dt)
            self.curiosity = min(1.0, self.curiosity + 0.020 * dt)
            self.last_stimulus = now

        # RF crowds are interesting, but not infinitely so.
        if visible_count:
            target = min(1.0, 0.24 + visible_count * 0.11)
            self.curiosity += (target - self.curiosity) * 0.035 * dt

        # Large light changes are also a small stimulus.
        if lux is not None:
            if self.last_lux is not None:
                delta = abs(lux - self.last_lux)
                if delta > max(30.0, self.last_lux * 0.45):
                    self.curiosity = min(1.0, self.curiosity + 0.035)
                    self.arousal = min(1.0, self.arousal + 0.025)
            self.last_lux = lux

        # Novelty / gesture attention pulses fade over time.
        self.new_device_pulse = max(0.0, self.new_device_pulse - 0.10 * dt)
        self.gesture_pulse = max(0.0, self.gesture_pulse - 0.22 * dt)
        if self.gesture_pulse <= 0.01:
            self.gesture_reason = ""

        # After stimulation curiosity slowly settles, but never to zero.
        if quiet_for > 30:
            self.curiosity -= 0.006 * dt

        self.curiosity = max(0.08, min(1.0, self.curiosity))
        self.arousal = max(0.02, min(1.0, self.arousal))

    def suggested_mood(self):
        if self.gesture_pulse > 0.20:
            return "curious", self.gesture_reason or "sudden movement"
        if self.new_device_pulse > 0.25:
            return "curious", "new RF identity"
        if self.curiosity >= 0.72:
            return "curious", "curiosity"
        if self.arousal >= 0.70:
            return "happy", "arousal"
        return None, None


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("collarpet")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=2_000_000,
            backupCount=5,
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"
        ))
        logger.addHandler(handler)

    return logger


LOGGER = setup_logging()


def log_event(kind, **fields):
    event = {
        "time": datetime.now().isoformat(timespec="milliseconds"),
        "kind": kind,
        **fields,
    }
    try:
        with EVENT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        LOGGER.warning("event log write failed: %s", e)


mind = PetMind()

devices: Dict[str, EFDevice] = {}
ble_env = BLEEnvironment()
esp = ESPState()
world = WorldState()
audio = AudioState(device=AUDIO_DEVICE)
neural = NeuralState()
remote = RemoteMirrorState()
remote_ble_device = None
neural_brain = None

if NeuralPetMind is not None and NEURAL_MODEL_FILE.exists():
    try:
        neural_brain = NeuralPetMind(NEURAL_MODEL_FILE, history=16)
        neural.available = True
        neural.held_since = time.monotonic()
    except Exception as e:
        neural.last_error = str(e)
        LOGGER.warning("Neural PetMind disabled: %s", e)
else:
    neural.last_error = (
        f"model missing: {NEURAL_MODEL_FILE}" if NeuralPetMind is not None
        else f"brain import failed: {_brain_import_error}"
    )
    LOGGER.warning("Neural PetMind unavailable: %s", neural.last_error)

stop_event = asyncio.Event()
ble_pause_requested = asyncio.Event()
ble_scanner_paused = asyncio.Event()
epaper_lock = asyncio.Lock()
force_epaper_refresh = asyncio.Event()
epaper_terminal_lock = asyncio.Event()

# Requested state from Linux. Later this can come from a physical ESP input.
STEALTH = False

# User-adjustable ceiling for the collar strip. 100 means the ESP firmware's
# normal configured brightness; lower values scale that ceiling down.
# Persist it so a Pi/runtime restart does not jump back to 100%.
COLLAR_LED_PERCENT = 100

def load_runtime_settings():
    global COLLAR_LED_PERCENT
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        COLLAR_LED_PERCENT = max(0, min(100, int(data.get("collar_led_percent", COLLAR_LED_PERCENT))))
    except Exception:
        pass

def save_runtime_settings():
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "version": 1,
            "collar_led_percent": int(COLLAR_LED_PERCENT),
            "saved": datetime.now().isoformat(timespec="seconds"),
        }, indent=2))
        tmp.replace(SETTINGS_FILE)
    except Exception as e:
        LOGGER.warning("settings save failed: %s", e)

load_runtime_settings()

# The UART manager is the ONLY owner of /dev/ttyS7.  Every other subsystem
# submits complete protocol lines here; none of them may touch pyserial.
esp_serial = None
esp_tx_queue = asyncio.Queue()
esp_tx_latest = {}
esp_tx_wakeup = asyncio.Event()

# High-rate/latest-state commands are coalesced so stale VU frames cannot
# build a backlog in front of control traffic.
ESP_COALESCE_PREFIXES = ("VU ", "MOOD ", "LEDLEVEL ")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def visible_devices(now=None) -> List[EFDevice]:
    if now is None:
        now = time.monotonic()
    return sorted(
        [d for d in devices.values() if now - d.last_seen <= DEVICE_TIMEOUT],
        key=lambda d: d.rssi,
        reverse=True,
    )


def choose_primary(items: List[EFDevice]) -> Optional[EFDevice]:
    if not items:
        return None

    if DISPLAY_PRIORITY == "STRONGEST":
        return items[0]

    badges = [d for d in items if d.is_badge]
    beacons = [d for d in items if d.is_beacon]

    if DISPLAY_PRIORITY == "BADGES":
        return badges[0] if badges else items[0]

    if DISPLAY_PRIORITY == "BEACONS":
        return beacons[0] if beacons else items[0]

    # AUTO
    if beacons and beacons[0].rssi >= -38:
        return beacons[0]
    if badges:
        return badges[0]
    return items[0]


def proximity_from_rssi(rssi: float) -> str:
    if rssi >= -45:
        return "VERY CLOSE"
    if rssi >= -60:
        return "CLOSE"
    if rssi >= -75:
        return "NEAR"
    if rssi >= -88:
        return "FAR"
    return "VERY FAR"


def safe_float(s: str, default=None):
    try:
        return float(s)
    except Exception:
        return default


def safe_int(s: str, default=0):
    try:
        return int(s)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Tail Company MiTail gear manager
# ---------------------------------------------------------------------------

class TailGearManager:
    """Non-blocking MiTail BLE owner with phone-friendly AUTO release.

    The remote/UI thread only enqueues actions.  All scanning, connecting and
    GATT writes happen in this task so gear failures cannot stall CollarPet's
    audio, UART, display or remote handling.
    """

    def __init__(self):
        self.queue = asyncio.Queue()
        self.client = None
        self.profile = None
        self.connected = False
        self.manual_hold = False
        self.last_activity = 0.0
        self.last_rx = ""
        self.last_error = ""
        self.battery = -1
        self.active_phase = 0
        self.active_last_send = 0.0
        self.learned = {}
        self._load()
        self._sync_globals()

    def _load(self):
        global GEAR_MAIN_ENABLED, TAIL_ENABLED, TAIL_ACTIVE_MODE, TAIL_WAG_KNOWN_SONG
        try:
            data = json.loads(GEAR_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        GEAR_MAIN_ENABLED = bool(data.get("main_enabled", GEAR_MAIN_ENABLED))
        TAIL_ENABLED = bool(data.get("tail_enabled", TAIL_ENABLED))
        TAIL_ACTIVE_MODE = bool(data.get("tail_active_mode", TAIL_ACTIVE_MODE))
        TAIL_WAG_KNOWN_SONG = bool(data.get("tail_wag_known_song", TAIL_WAG_KNOWN_SONG))
        tail = data.get("tail", {})
        self.learned = tail if isinstance(tail, dict) else {}

    def _save(self):
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                data = json.loads(GEAR_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, dict): data = {}
            except Exception:
                data = {}
            data.update({"main_enabled": bool(GEAR_MAIN_ENABLED), "tail_enabled": bool(TAIL_ENABLED), "tail": self.learned})
            GEAR_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            LOGGER.warning("gear settings save failed: %s", e)

    def _sync_globals(self):
        global TAIL_CONNECTED
        TAIL_CONNECTED = bool(self.connected and GEAR_MAIN_ENABLED and TAIL_ENABLED)

    def submit(self, op, value=None):
        try:
            self.queue.put_nowait((str(op).upper(), value))
        except Exception as e:
            LOGGER.warning("tail queue failed: %s", e)

    def set_main_enabled(self, enabled):
        global GEAR_MAIN_ENABLED
        GEAR_MAIN_ENABLED = bool(enabled)
        self._save()
        self._sync_globals()
        if not GEAR_MAIN_ENABLED:
            self.submit("RELEASE")

    def set_tail_enabled(self, enabled):
        global TAIL_ENABLED
        TAIL_ENABLED = bool(enabled)
        self._save()
        self._sync_globals()
        if not TAIL_ENABLED:
            self.submit("RELEASE")

    def set_active_mode(self, enabled):
        global TAIL_ACTIVE_MODE
        TAIL_ACTIVE_MODE = bool(enabled)
        self._save()
        if TAIL_ACTIVE_MODE: self.submit("ACTIVE_CONNECT")
        else: self.manual_hold = False

    def set_song_wag(self, enabled):
        global TAIL_WAG_KNOWN_SONG
        TAIL_WAG_KNOWN_SONG = bool(enabled)
        self._save()

    def _disconnected(self, _client):
        self.client = None
        self.connected = False
        self.profile = None
        self.manual_hold = False
        self._sync_globals()
        print("[TAIL] disconnected")
        force_epaper_refresh.set()

    @staticmethod
    def _adv_is_mitail(device, adv):
        name = (getattr(adv, "local_name", None) or getattr(device, "name", None) or "").lower()
        services = {str(u).lower() for u in (getattr(adv, "service_uuids", None) or [])}
        return name == "mitail" or any(p["service"] in services for p in MITAIL_PROFILES)

    async def _scan_candidates(self, timeout=8.0):
        found = {}

        def cb(device, adv):
            if self._adv_is_mitail(device, adv):
                addr = getattr(device, "address", "")
                if addr:
                    found[addr] = (device, adv)

        scanner = BleakScanner(detection_callback=cb)
        try:
            await scanner.start()
            await asyncio.sleep(timeout)
        finally:
            try:
                await scanner.stop()
            except Exception:
                pass
        return list(found.values())

    async def learn(self):
        print("[TAIL] learn scan started (8 sec)...")
        candidates = await self._scan_candidates(8.0)
        if not candidates:
            self.last_error = "no MiTail found"
            print("[TAIL] learn failed: no MiTail found")
            return False
        if len(candidates) != 1:
            self.last_error = f"{len(candidates)} MiTails found; refusing ambiguous learn"
            print(f"[TAIL] learn failed: {len(candidates)} MiTails found; power off the others and retry")
            return False

        device, adv = candidates[0]
        address = getattr(device, "address", "")
        name = getattr(adv, "local_name", None) or getattr(device, "name", None) or "mitail"
        service_uuids = [str(u).lower() for u in (getattr(adv, "service_uuids", None) or [])]
        self.learned = {
            "address": address,
            "name": name,
            "service_uuids": service_uuids,
        }
        self._save()
        self.last_error = ""
        print(f"[TAIL] learned {name} @ {address}")
        log_event("tail_learned", address=address, name=name)
        return True

    async def forget(self):
        await self.release()
        self.learned = {}
        self._save()
        self.last_error = ""
        print("[TAIL] learned device forgotten")
        log_event("tail_forgotten")

    async def _resolve_device(self):
        address = str(self.learned.get("address", "")).strip()
        if not address:
            self.last_error = "tail not learned"
            return None

        # Prefer the saved identity.  Do not silently adopt another nearby
        # 'mitail' just because its name matches.
        try:
            device = await BleakScanner.find_device_by_address(address, timeout=4.0)
        except Exception:
            device = None
        if device is not None:
            return device

        self.last_error = "learned tail not advertising"
        return None

    def _pick_profile(self, client):
        services = {str(s.uuid).lower() for s in client.services}
        for profile in MITAIL_PROFILES:
            if profile["service"] in services:
                return profile
        return None

    async def connect(self, manual=False):
        if not GEAR_MAIN_ENABLED or not TAIL_ENABLED:
            self.last_error = "tail disabled"
            return False
        if self.connected and self.client is not None:
            if manual:
                self.manual_hold = True
            self.last_activity = time.monotonic()
            return True

        device = await self._resolve_device()
        if device is None:
            print(f"[TAIL] connect skipped: {self.last_error}")
            return False

        try:
            print(f"[TAIL] connecting {self.learned.get('name','mitail')} @ {self.learned.get('address','?')}...")
            client = BleakClient(device, disconnected_callback=self._disconnected)
            await client.connect()
            profile = self._pick_profile(client)
            if profile is None:
                await client.disconnect()
                self.last_error = "unsupported MiTail GATT profile"
                print("[TAIL] unsupported GATT profile")
                return False

            self.client = client
            self.profile = profile
            try:
                await client.start_notify(profile["tx"], self._notify)
            except Exception as e:
                print(f"[TAIL] TX notify unavailable: {e}")

            self.connected = True
            self.manual_hold = bool(manual)
            self.last_activity = time.monotonic()
            self.last_error = ""
            self._sync_globals()
            force_epaper_refresh.set()
            print(f"[TAIL] connected profile={profile['name']}")
            log_event("tail_connected", profile=profile["name"])
            return True
        except Exception as e:
            self.client = None
            self.profile = None
            self.connected = False
            self.manual_hold = False
            self.last_error = str(e)
            self._sync_globals()
            print(f"[TAIL] connect failed/busy: {e}")
            return False

    def _notify(self, _sender, data):
        try:
            text = bytes(data).decode("utf-8", errors="ignore").strip()
        except Exception:
            return
        if not text:
            return
        self.last_rx = text
        print(f"[TAIL RX] {text}")

        # New TailControl BATT returns an integer percentage.  Older MiTail
        # firmware may answer BATTn/Bn using 0..4 bars.
        token = text.strip().upper()
        try:
            if token.isdigit():
                self.battery = max(0, min(100, int(token)))
            elif token.startswith("BATT") and token[4:].isdigit():
                n = int(token[4:])
                self.battery = n if n > 4 else n * 25
            elif token.startswith("B") and token[1:].isdigit():
                n = int(token[1:])
                self.battery = n if n > 4 else n * 25
        except Exception:
            pass

    async def _write(self, command):
        if not await self.connect(manual=False):
            return False
        try:
            # TailControl's console is line-oriented; trailing whitespace is
            # explicitly ignored by the firmware.  Newline also works with the
            # original MiTail console parser.
            payload = (command.strip().upper() + "\n").encode("ascii")
            await self.client.write_gatt_char(self.profile["rx"], payload, response=False)
            self.last_activity = time.monotonic()
            print(f"[TAIL TX] {command.strip().upper()}")
            log_event("tail_command", command=command.strip().upper())
            return True
        except Exception as e:
            self.last_error = str(e)
            print(f"[TAIL] write failed: {e}")
            return False

    async def _active_tick(self):
        if not (GEAR_MAIN_ENABLED and TAIL_ENABLED and TAIL_ACTIVE_MODE):
            return
        now = time.monotonic()
        if now - self.active_last_send < 0.50:
            return
        if not await self.connect(manual=True):
            return

        mood = str(world.mood).lower()
        moving = bool(getattr(esp.imu, "moving", False))
        self.active_phase = (self.active_phase + 1) & 3
        amp = 1
        if mood in ("happy", "excited", "foxfound"): amp = 2
        elif mood in ("sleep", "calm"): amp = 0
        elif moving: amp = 2

        sign = 1 if self.active_phase < 2 else -1
        a = max(2, min(6, 4 + sign * amp))
        b = max(2, min(6, 4 - sign * amp))
        ticks = 20 if amp <= 1 else 15
        await self._write(f"DSSP A{a}B{b}L{ticks}M{ticks}H0")
        self.active_last_send = now

    async def release(self):
        self.manual_hold = False
        client = self.client
        self.client = None
        self.connected = False
        self.profile = None
        self._sync_globals()
        force_epaper_refresh.set()
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        print("[TAIL] released for phone")
        log_event("tail_released")

    async def run(self):
        print("[TAIL] manager ready")
        while not stop_event.is_set():
            try:
                try:
                    op, value = await asyncio.wait_for(self.queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    op = None
                    value = None

                if op == "LEARN":
                    await self.learn()
                elif op == "FORGET":
                    await self.forget()
                elif op == "CONNECT":
                    await self.connect(manual=True)
                elif op == "RELEASE":
                    await self.release()
                elif op == "MOVE" and str(value).upper() in TAIL_MOVE_COMMANDS:
                    await self._write(str(value).upper())
                elif op == "LED" and str(value).upper() in TAIL_LED_COMMANDS:
                    await self._write(str(value).upper())
                elif op == "BATT":
                    await self._write("BATT")
                elif op == "ACTIVE_CONNECT":
                    await self.connect(manual=True)

                await self._active_tick()

                if self.connected and not TAIL_ACTIVE_MODE and not self.manual_hold:
                    idle = time.monotonic() - self.last_activity
                    if idle >= TAIL_IDLE_DISCONNECT_SECONDS:
                        print(f"[TAIL] auto release after {idle:.0f}s idle")
                        await self.release()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_error = str(e)
                LOGGER.warning("tail manager error: %s", e)
                await asyncio.sleep(1.0)

        await self.release()



class EarGearManager:
    """EarGear 2 BLE owner. Setup lives on the master remote; actions may relay from nodes."""
    def __init__(self):
        self.queue=asyncio.Queue(); self.client=None; self.profile=None; self.connected=False
        self.manual_hold=False; self.last_activity=0.0; self.last_rx=""; self.last_error=""; self.battery=-1; self.learned={}
        self.active_phase=0; self.active_last_send=0.0
        self._load(); self._sync_globals()

    def _load(self):
        global EARS_ENABLED, EARS_ACTIVE_MODE
        try:
            data=json.loads(GEAR_FILE.read_text(encoding="utf-8"))
            if not isinstance(data,dict): data={}
        except Exception: data={}
        EARS_ENABLED=bool(data.get("ears_enabled",EARS_ENABLED))
        EARS_ACTIVE_MODE=bool(data.get("ears_active_mode",EARS_ACTIVE_MODE))
        ears=data.get("ears",{}); self.learned=ears if isinstance(ears,dict) else {}

    def _save(self):
        try:
            STATE_DIR.mkdir(parents=True,exist_ok=True)
            try:
                data=json.loads(GEAR_FILE.read_text(encoding="utf-8"))
                if not isinstance(data,dict): data={}
            except Exception: data={}
            data.update({
                "main_enabled":bool(GEAR_MAIN_ENABLED),
                "ears_enabled":bool(EARS_ENABLED),
                "ears_active_mode":bool(EARS_ACTIVE_MODE),
                "ears":self.learned,
            })
            GEAR_FILE.write_text(json.dumps(data,indent=2),encoding="utf-8")
        except Exception as e: LOGGER.warning("ear settings save failed: %s",e)

    def _sync_globals(self):
        global EARS_CONNECTED
        EARS_CONNECTED=bool(self.connected and GEAR_MAIN_ENABLED and EARS_ENABLED)

    def submit(self,op,value=None):
        try: self.queue.put_nowait((str(op).upper(),value))
        except Exception as e: LOGGER.warning("ears queue failed: %s",e)

    def set_ears_enabled(self,enabled):
        global EARS_ENABLED
        EARS_ENABLED=bool(enabled); self._save(); self._sync_globals()
        if not EARS_ENABLED: self.submit("RELEASE")

    def set_active_mode(self,enabled):
        global EARS_ACTIVE_MODE
        EARS_ACTIVE_MODE=bool(enabled); self._save()
        if EARS_ACTIVE_MODE: self.submit("ACTIVE_CONNECT")
        else: self.manual_hold=False

    def _disconnected(self,_client):
        self.client=None; self.profile=None; self.connected=False; self.manual_hold=False; self._sync_globals()
        print("[EARS] disconnected"); force_epaper_refresh.set()

    @staticmethod
    def _adv_is_eargear(device,adv):
        name=(getattr(adv,"local_name",None) or getattr(device,"name",None) or "").strip().lower()
        services={str(u).lower() for u in (getattr(adv,"service_uuids",None) or [])}
        # Current unified TailControl service is shared with tails, so name is
        # authoritative there; the old EG2-specific service is unambiguous.
        return name=="eg2" or EARGEAR_PROFILES[1]["service"] in services

    async def _scan_candidates(self,timeout=8.0):
        found={}
        def cb(device,adv):
            if self._adv_is_eargear(device,adv):
                addr=getattr(device,"address","")
                if addr: found[addr]=(device,adv)
        scanner=BleakScanner(detection_callback=cb)
        try:
            await scanner.start(); await asyncio.sleep(timeout)
        finally:
            try: await scanner.stop()
            except Exception: pass
        return list(found.values())

    async def learn(self):
        print("[EARS] learn scan started (8 sec)...")
        candidates=await self._scan_candidates(8.0)
        if not candidates:
            self.last_error="no EarGear 2 found"; print("[EARS] learn failed: no EG2 found"); return False
        if len(candidates)!=1:
            self.last_error=f"{len(candidates)} EarGear devices found"; print(f"[EARS] learn failed: {len(candidates)} EG2 devices found"); return False
        device,adv=candidates[0]; address=getattr(device,"address","")
        name=getattr(adv,"local_name",None) or getattr(device,"name",None) or "EG2"
        self.learned={"address":address,"name":name,"service_uuids":[str(u).lower() for u in (getattr(adv,"service_uuids",None) or [])]}
        self._save(); self.last_error=""; print(f"[EARS] learned {name} @ {address}"); log_event("ears_learned",address=address,name=name); return True

    async def forget(self):
        await self.release(); self.learned={}; self._save(); self.last_error=""; print("[EARS] learned device forgotten"); log_event("ears_forgotten")

    async def _resolve_device(self):
        address=str(self.learned.get("address","")).strip()
        if not address: self.last_error="ears not learned"; return None
        try: device=await BleakScanner.find_device_by_address(address,timeout=4.0)
        except Exception: device=None
        if device is None: self.last_error="learned ears not advertising"
        return device

    def _pick_profile(self,client):
        services={str(s.uuid).lower() for s in client.services}
        for p in EARGEAR_PROFILES:
            if p["service"] in services: return p
        return None

    async def connect(self,manual=False):
        if not GEAR_MAIN_ENABLED or not EARS_ENABLED: self.last_error="ears disabled"; return False
        if self.connected and self.client is not None:
            if manual: self.manual_hold=True
            self.last_activity=time.monotonic(); return True
        device=await self._resolve_device()
        if device is None: print(f"[EARS] connect skipped: {self.last_error}"); return False
        try:
            print(f"[EARS] connecting {self.learned.get('name','EG2')} @ {self.learned.get('address','?')}...")
            client=BleakClient(device,disconnected_callback=self._disconnected); await client.connect()
            profile=self._pick_profile(client)
            if profile is None:
                await client.disconnect(); self.last_error="unsupported EarGear GATT profile"; return False
            self.client=client; self.profile=profile
            try: await client.start_notify(profile["tx"],self._notify)
            except Exception as e: print(f"[EARS] TX notify unavailable: {e}")
            self.connected=True; self.manual_hold=bool(manual); self.last_activity=time.monotonic(); self.last_error=""
            self._sync_globals(); force_epaper_refresh.set(); print(f"[EARS] connected profile={profile['name']}"); log_event("ears_connected",profile=profile["name"]); return True
        except Exception as e:
            self.client=None; self.profile=None; self.connected=False; self.manual_hold=False; self.last_error=str(e); self._sync_globals()
            print(f"[EARS] connect failed/busy: {e}"); return False

    def _notify(self,_sender,data):
        try: text=bytes(data).decode("utf-8",errors="ignore").strip()
        except Exception: return
        if not text: return
        self.last_rx=text; print(f"[EARS RX] {text}")
        token=text.upper()
        try:
            if token.isdigit(): self.battery=max(0,min(100,int(token)))
            elif token.startswith("BATT") and token[4:].isdigit(): self.battery=max(0,min(100,int(token[4:])))
        except Exception: pass

    async def _write(self,command):
        if not await self.connect(manual=False): return False
        try:
            payload=(command.strip().upper()+"\n").encode("ascii")
            await self.client.write_gatt_char(self.profile["rx"],payload,response=False)
            self.last_activity=time.monotonic(); print(f"[EARS TX] {command.strip().upper()}"); log_event("ears_command",command=command.strip().upper()); return True
        except Exception as e: self.last_error=str(e); print(f"[EARS] write failed: {e}"); return False

    async def _active_tick(self):
        if not (GEAR_MAIN_ENABLED and EARS_ENABLED and EARS_ACTIVE_MODE):
            return
        now=time.monotonic()
        if now-self.active_last_send < 0.50:
            return
        if not await self.connect(manual=True):
            return

        mood=str(world.mood).lower()
        self.active_phase=(self.active_phase+1)&3
        if mood in ("curious","foxfound"):
            a,b=(3,5) if self.active_phase<2 else (4,4)
        elif mood in ("annoyed","alert"):
            a,b=(5,3)
        elif mood in ("sleep","calm"):
            a,b=(4,4)
        else:
            a,b=(4,5) if self.active_phase<2 else (5,4)
        await self._write(f"DSSP A{a}B{b}L20M20H0")
        self.active_last_send=now

    async def release(self):
        self.manual_hold=False; client=self.client; self.client=None; self.profile=None; self.connected=False; self._sync_globals(); force_epaper_refresh.set()
        if client is not None:
            try: await client.disconnect()
            except Exception: pass
        print("[EARS] released for phone"); log_event("ears_released")

    async def run(self):
        print("[EARS] manager ready")
        while not stop_event.is_set():
            try:
                try: op,value=await asyncio.wait_for(self.queue.get(),timeout=0.5)
                except asyncio.TimeoutError: op=value=None
                if op=="LEARN": await self.learn()
                elif op=="FORGET": await self.forget()
                elif op=="CONNECT": await self.connect(manual=True)
                elif op=="RELEASE": await self.release()
                elif op=="CMD" and str(value).upper() in EAR_COMMANDS: await self._write(str(value).upper())
                elif op=="BATT": await self._write("BATT")
                elif op=="ACTIVE_CONNECT": await self.connect(manual=True)

                await self._active_tick()

                if self.connected and (not GEAR_MAIN_ENABLED or not EARS_ENABLED): await self.release()
                elif self.connected and not EARS_ACTIVE_MODE and not self.manual_hold and time.monotonic()-self.last_activity>=EAR_IDLE_DISCONNECT_SECONDS:
                    print("[EARS] auto release after idle"); await self.release()
            except asyncio.CancelledError: break
            except Exception as e: self.last_error=str(e); LOGGER.warning("ears manager error: %s",e); await asyncio.sleep(1.0)
        await self.release()

gear_manager = TailGearManager()
ear_manager = EarGearManager()

# ---------------------------------------------------------------------------
# BLE / EF28
# ---------------------------------------------------------------------------

def decode_ef28(payload: bytes):
    if len(payload) < 8 or payload[0] != 0x02:
        return None

    dev_type = chr(payload[1]) if payload[1] in (0x42, 0x44) else "?"
    dev_id = int.from_bytes(payload[2:6], "little")
    flags = payload[6]
    tx_power = struct.unpack("b", payload[7:8])[0]
    return dev_type, dev_id, flags, tx_power


def ble_detection_callback(device, advertisement_data):
    global remote_ble_device

    try:
        address = getattr(device, "address", "?")
        raw_rssi = getattr(advertisement_data, "rssi", -100)
        local_name = (
            getattr(advertisement_data, "local_name", None)
            or getattr(device, "name", None)
            or ""
        )
        service_uuids = [u.lower() for u in (getattr(advertisement_data, "service_uuids", None) or [])]

        # Our own gear/display should not count as anonymous RF environment
        # texture.  Remember the BLEDevice object for remote_mirror_task().
        if local_name.lower() == "mitail" or any(p["service"] in service_uuids for p in MITAIL_PROFILES):
            return

        if local_name == REMOTE_DEVICE_NAME or REMOTE_SERVICE_UUID.lower() in service_uuids:
            remote_ble_device = device
            remote.discovered = True
            remote.address = address
            remote.rssi = int(raw_rssi)
            remote.last_seen = time.monotonic()
            return

        ble_env.observe(address, raw_rssi)

        payload = advertisement_data.manufacturer_data.get(EF28_MFG_ID)
        if payload is None:
            return

        payload = bytes(payload)
        decoded = decode_ef28(payload)
        if not decoded:
            return

        dev_type, dev_id, flags, tx_power = decoded
        name = (
            advertisement_data.local_name
            or getattr(device, "name", None)
            or address
        )
        now = time.monotonic()

        old = devices.get(address)
        if old:
            rssi = RSSI_ALPHA * raw_rssi + (1.0 - RSSI_ALPHA) * old.rssi
        else:
            rssi = float(raw_rssi)
            # Only the first sighting for this runtime reaches the mind here.
            mind.observe_device(dev_id, dev_type, name)

        devices[address] = EFDevice(
            address=address,
            name=name,
            dev_type=dev_type,
            dev_id=dev_id,
            flags=flags,
            tx_power=tx_power,
            rssi=rssi,
            raw_rssi=raw_rssi,
            last_seen=now,
            raw_payload=payload,
        )

    except Exception as e:
        print(f"[BLE] decode error: {e}")


async def ble_task():
    """Passive environment scan, paused while a remote owns the BLE adapter."""
    scanner = None
    scanning = False

    while not stop_event.is_set():
        try:
            if ble_pause_requested.is_set():
                if scanner is not None and scanning:
                    try:
                        await scanner.stop()
                    finally:
                        scanning = False
                        print("[BLE] scanner paused for remote link")
                ble_scanner_paused.set()
                await asyncio.sleep(0.1)
                continue

            ble_scanner_paused.clear()

            if scanner is None:
                scanner = BleakScanner(detection_callback=ble_detection_callback)

            if not scanning:
                await scanner.start()
                scanning = True
                print("[BLE] scanner started")

            await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[BLE] scanner error: {e}; retrying")
            if scanner is not None and scanning:
                try:
                    await scanner.stop()
                except Exception:
                    pass
            scanner = None
            scanning = False
            ble_scanner_paused.clear()
            await asyncio.sleep(2.0)

    if scanner is not None and scanning:
        try:
            await scanner.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Heltec external e-paper mirror
# ---------------------------------------------------------------------------


def current_mood_confidence_percent():
    """Confidence for the mood actually shown.

    100% is used for authoritative deterministic overrides such as stealth
    and a close fox beacon. Neural moods use the probability of the held
    expression when available. Non-neural fallback moods report unknown (-1).
    """
    if STEALTH or world.reason in ("stealth", "fox beacon"):
        return 100

    if world.reason.startswith("neural") and neural.available:
        for expression, probability in neural.top:
            if expression == world.mood:
                return int(round(max(0.0, min(1.0, probability)) * 100.0))

        if neural.expression == world.mood:
            return int(round(max(0.0, min(1.0, neural.confidence)) * 100.0))

    return -1


def _pet_text(value: str, max_chars: int):
    clean = "".join(ch if 32 <= ord(ch) < 127 and ch not in "|,\r\n" else " " for ch in str(value))
    return " ".join(clean.split())[:max_chars]


def current_pi_battery_percent():
    # Reserved for the collar-computer battery monitor. Hardware is not fitted
    # yet, so -1 means unknown/unavailable and the UI draws a crossed battery.
    return -1


def remote_payload_signature():
    """State shared with the master remote. Detailed telemetry stays BLE-only.

    The LoRa master rebroadcasts only pet/UI state to secondary remotes; GPS,
    pulse, environment and BLE-density diagnostics are intentionally excluded
    from the LoRa PET packet.
    """
    items = visible_devices()
    b = ble_env.stats()
    ef_badges = sum(1 for d in items if d.is_badge)
    bpm = int(round(esp.pulse.bpm)) if esp.pulse.valid and esp.pulse.bpm is not None else -1
    lux = int(round(esp.light_lux / 2.0) * 2) if esp.light_lux is not None else -1
    temp_tenths = int(round(esp.temp_c * 10.0)) if esp.temp_c is not None else -32768
    pressure = int(round(esp.pressure_hpa)) if esp.pressure_hpa is not None else -1
    song_recent = bool(audio.song_title and time.monotonic() - audio.song_last_seen <= 8.0)

    return (
        world.mood, current_mood_confidence_percent(), VERSION,
        int(b["density"]), ef_badges, bpm, bool(esp.pulse.valid), lux,
        temp_tenths, pressure, bool(esp.connected and esp.handshake),
        bool(esp.gps.fix), bool(STEALTH), int(COLLAR_LED_PERCENT),
        audio.song_artist if song_recent else "",
        audio.song_title if song_recent else "",
        int(round(audio.song_confidence * 20.0) * 5) if song_recent else 0,
        _pet_text(world.activity, 18), _pet_text(world.reason, 24),
        current_pi_battery_percent(),
        bool(GEAR_MAIN_ENABLED), bool(TAIL_ENABLED), bool(TAIL_CONNECTED),
        bool(EARS_ENABLED), bool(EARS_CONNECTED),
        bool(TAIL_ACTIVE_MODE), bool(TAIL_WAG_KNOWN_SONG), bool(EARS_ACTIVE_MODE),
    )


async def remote_write(client, command: str):
    await client.write_gatt_char(
        REMOTE_DISPLAY_UUID,
        command.encode("utf-8"),
        response=False,
    )


def _remote_song_line(value: str, max_chars: int):
    """ASCII-safe one-line text that stays below the default BLE ATT payload."""
    clean = "".join(ch if 32 <= ord(ch) < 127 and ch not in "|,\r\n" else " " for ch in str(value))
    return " ".join(clean.split())[:max_chars]


async def remote_send_state(client, sig):
    (
        mood, mood_confidence, version, ble_count, ef_count, bpm, pulse_valid,
        lux, temp_tenths, pressure, esp_ok, gps_fix, stealth, collar_led_percent,
        song_artist, song_title, song_confidence, activity, reason, pi_battery,
        gear_enabled, tail_enabled, tail_connected, ears_enabled, ears_connected,
        tail_active, tail_song_wag, ears_active,
    ) = sig

    await remote_write(client, f"M|{mood}|{mood_confidence}")
    await remote_write(client, f"V|{version}")
    await remote_write(client, f"R|{ble_count}|{ef_count}")
    await remote_write(client, f"P|{bpm}|{1 if pulse_valid else 0}")
    await remote_write(client, f"T|{temp_tenths}|{pressure}")
    await remote_write(client, f"L|{collar_led_percent}")
    await remote_write(
        client,
        f"E|{lux}|{1 if esp_ok else 0}|{1 if gps_fix else 0}|{1 if stealth else 0}",
    )

    # V9 protocol: two actual song display lines.
    # S0 carries confidence + artist, S1 carries title.
    artist_line = _remote_song_line(song_artist, 20)
    title_line = _remote_song_line(song_title, 20)
    await remote_write(client, f"S0|{song_confidence}|{artist_line}")
    await remote_write(client, f"S1|{title_line}")
    await remote_write(client, f"D|{_remote_song_line(activity, 18)}|{_remote_song_line(reason, 22)}")
    await remote_write(client, f"B|{pi_battery}")
    await remote_write(
        client,
        f"G|{1 if gear_enabled else 0}|{1 if tail_enabled else 0}|{1 if tail_connected else 0}|"
        f"{1 if ears_enabled else 0}|{1 if ears_connected else 0}|"
        f"{1 if tail_active else 0}|{1 if tail_song_wag else 0}|{1 if ears_active else 0}",
    )

    await remote_write(client, "A|1")
    await remote_write(client, "C")


async def remote_power_action(action: str):
    """Execute an explicitly confirmed remote power command."""
    action = action.upper()

    if action not in ("SHUTDOWN", "REBOOT"):
        return

    print(f"[REMOTE CMD] power action requested: {action}")
    log_event("remote_command", command="power", value=action)

    # The collar's own e-paper is persistent. Once a terminal power action
    # starts, no normal/background refresh is allowed to touch it again.
    epaper_terminal_lock.set()
    force_epaper_refresh.clear()

    try:
        async with epaper_lock:
            await asyncio.to_thread(epaper_power_screen, action)
    except Exception as e:
        LOGGER.warning("power screen failed: %s", e)

    if action == "SHUTDOWN":
        # Ask the controller to turn off lights/haptics, suspend its I2C
        # sensors and enter deep sleep. A real power-cycle/reset wakes it.
        await esp_send("POWERDOWN")
        await asyncio.sleep(0.45)
    else:
        # Tell the ESP this disappearance is intentional. The controller stays
        # alive in its dedicated reboot animation until the new Linux startup
        # reaches BOOT START.
        await esp_send("REBOOT_MODE")
        await asyncio.sleep(0.45)

    command = "poweroff" if action == "SHUTDOWN" else "reboot"

    try:
        proc = await asyncio.create_subprocess_exec("systemctl", command)
        await proc.wait()
    except Exception as e:
        LOGGER.warning("remote power action failed: %s", e)
        print(f"[REMOTE CMD] power action failed: {e}")


def apply_remote_pet_action(action: str):
    """Translate an intentional remote interaction into PetMind stimulation."""
    now = time.monotonic()
    action = action.upper()

    if action == "ATTENTION":
        mind.arousal = min(1.0, mind.arousal + 0.22)
        mind.curiosity = min(1.0, mind.curiosity + 0.18)
        mind.boredom = max(0.0, mind.boredom - 0.30)
        mind.gesture_pulse = max(mind.gesture_pulse, 0.70)
        mind.gesture_reason = "remote attention poke"
        mind.last_stimulus = now
        asyncio.create_task(esp_send("HAPTIC ATTENTION"))
        log_event("remote_pet", action="attention")
        return

    if action == "WAKE":
        mind.arousal = min(1.0, mind.arousal + 0.45)
        mind.curiosity = min(1.0, mind.curiosity + 0.12)
        mind.boredom = max(0.0, mind.boredom - 0.55)
        mind.gesture_pulse = max(mind.gesture_pulse, 0.95)
        mind.gesture_reason = "remote wake"
        mind.last_stimulus = now
        asyncio.create_task(esp_send("HAPTIC WAKE"))
        log_event("remote_pet", action="wake")
        return

    if action == "CALM":
        mind.arousal = max(0.02, mind.arousal - 0.40)
        mind.curiosity = max(0.08, mind.curiosity - 0.10)
        mind.gesture_pulse = 0.0
        mind.gesture_reason = ""
        log_event("remote_pet", action="calm")
        return


def remote_command_handler(sender, data):
    """Handle menu/remote-control notifications from the Heltec."""
    global STEALTH, COLLAR_LED_PERCENT, GEAR_MAIN_ENABLED, TAIL_ENABLED

    try:
        command = bytes(data).decode("utf-8", errors="ignore").strip()
    except Exception:
        return

    if not command:
        return

    print(f"[REMOTE CMD] {command}")
    parts = command.split("|")

    if len(parts) >= 2 and parts[0] == "STEALTH":
        requested = parts[1].strip() in ("1", "ON", "TRUE", "YES")

        if requested != STEALTH:
            STEALTH = requested
            print(f"[REMOTE CMD] stealth -> {STEALTH}")
            log_event("remote_command", command="stealth", value=STEALTH)

        return

    if len(parts) >= 2 and parts[0] == "COLLAR_LED":
        try:
            requested = int(parts[1])
        except ValueError:
            return

        requested = max(0, min(100, requested))
        COLLAR_LED_PERCENT = requested
        save_runtime_settings()
        print(f"[REMOTE CMD] collar LED -> {COLLAR_LED_PERCENT}%")
        log_event("remote_command", command="collar_led", value=COLLAR_LED_PERCENT)
        return

    if parts[0] == "FLASHBANG":
        style = parts[1].upper() if len(parts) >= 2 else "WHITE"
        if style not in ("WHITE", "COLOR"):
            style = "WHITE"
        asyncio.create_task(esp_send(f"FLASHBANG {style}"))
        log_event("remote_command", command="flashbang", style=style.lower())
        return

    if parts[0] == "FIND":
        asyncio.create_task(esp_send("FIND"))
        log_event("remote_command", command="find_collar")
        return

    if len(parts) >= 2 and parts[0] == "PET":
        apply_remote_pet_action(parts[1])
        return

    if len(parts) >= 2 and parts[0] == "GEAR":
        # Configuration options are only exposed by master firmware.  The Pi
        # still validates values and never auto-learns an arbitrary nearby tail.
        if parts[1] == "ENABLE" and len(parts) >= 3:
            enabled = parts[2].strip() in ("1", "ON", "TRUE", "YES")
            gear_manager.set_main_enabled(enabled)
            if not enabled: ear_manager.submit("RELEASE")
            log_event("gear_config", item="main", enabled=GEAR_MAIN_ENABLED)
            return
        if parts[1] == "TAIL" and len(parts) >= 3:
            action = parts[2].upper()
            if action == "ENABLE" and len(parts) >= 4:
                gear_manager.set_tail_enabled(parts[3].strip() in ("1", "ON", "TRUE", "YES"))
            elif action == "CONNECT":
                gear_manager.submit("CONNECT")
            elif action == "RELEASE":
                gear_manager.submit("RELEASE")
            elif action == "LEARN":
                gear_manager.submit("LEARN")
            elif action == "FORGET":
                gear_manager.submit("FORGET")
            elif action == "BATT":
                gear_manager.submit("BATT")
            return
        if parts[1] == "EARS" and len(parts) >= 3:
            action = parts[2].upper()
            if action == "ENABLE" and len(parts) >= 4:
                ear_manager.set_ears_enabled(parts[3].strip() in ("1", "ON", "TRUE", "YES"))
            elif action == "ACTIVE" and len(parts) >= 4:
                ear_manager.set_active_mode(parts[3].strip() in ("1", "ON", "TRUE", "YES"))
            elif action == "CONNECT": ear_manager.submit("CONNECT")
            elif action == "RELEASE": ear_manager.submit("RELEASE")
            elif action == "LEARN": ear_manager.submit("LEARN")
            elif action == "FORGET": ear_manager.submit("FORGET")
            elif action == "BATT": ear_manager.submit("BATT")
            return

    if len(parts) >= 3 and parts[0] == "EAR":
        kind = parts[1].upper(); value = parts[2].upper()
        if kind == "CMD" and value in EAR_COMMANDS: ear_manager.submit("CMD", value)
        return

    if len(parts) >= 3 and parts[0] == "TAIL":
        kind = parts[1].upper()
        value = parts[2].upper()
        if kind == "MOVE" and value in TAIL_MOVE_COMMANDS:
            gear_manager.submit("MOVE", value)
            return
        if kind == "LED" and value in TAIL_LED_COMMANDS:
            gear_manager.submit("LED", value)
            return

    if len(parts) >= 2 and parts[0] == "DISPLAY":
        action = parts[1].upper()
        if action == "REFRESH":
            if not epaper_terminal_lock.is_set():
                force_epaper_refresh.set()
                log_event("remote_command", command="display_refresh")
        return

    if len(parts) >= 2 and parts[0] == "HAPTIC":
        effect = parts[1].upper()
        if effect in ("CLICK", "DOUBLE", "FOX", "ATTENTION", "WAKE"):
            asyncio.create_task(esp_send(f"HAPTIC {effect}"))
            log_event("remote_command", command="haptic", value=effect)
        return

    if len(parts) >= 2 and parts[0] == "POWER":
        action = parts[1].upper()
        if action in ("SHUTDOWN", "REBOOT"):
            asyncio.create_task(remote_power_action(action))
        return

    if len(parts) >= 3 and parts[0] == "BUTTON":
        log_event("remote_button", button=parts[1], action=parts[2])
        return

    LOGGER.info("unknown remote command: %s", command)


async def remote_mirror_task():
    global remote_ble_device

    while not stop_event.is_set():
        device = remote_ble_device
        now = time.monotonic()

        # Discovery is supplied by the already-running environmental scanner.
        if device is None or not remote.last_seen or now - remote.last_seen > REMOTE_DISCOVERY_TIMEOUT:
            remote.connected = False
            await asyncio.sleep(0.5)
            continue

        try:
            print(f"[REMOTE] reserving BLE adapter for {remote.address or getattr(device, 'address', '?')}...")
            ble_pause_requested.set()
            try:
                await asyncio.wait_for(ble_scanner_paused.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                print("[REMOTE] scanner pause timed out; trying connection anyway")

            # Give BlueZ a brief moment to finish StopDiscovery before Connect().
            await asyncio.sleep(0.25)
            print(f"[REMOTE] connecting {remote.address or getattr(device, 'address', '?')}...")

            async with BleakClient(device) as client:
                remote.connected = bool(client.is_connected)
                remote.error = ""
                remote.last_payload = None
                remote.last_send = 0.0

                print(f"[REMOTE] mirror connected RSSI={remote.rssi}dBm")
                log_event("remote_mirror_connected", address=remote.address)

                try:
                    # Read first so the log proves that the command
                    # characteristic itself exists before subscribing.
                    ready = await client.read_gatt_char(REMOTE_COMMAND_UUID)
                    print(
                        "[REMOTE] command characteristic="
                        + bytes(ready).decode("utf-8", errors="ignore")
                    )

                    await client.start_notify(
                        REMOTE_COMMAND_UUID,
                        remote_command_handler,
                    )
                    print("[REMOTE] button/command notifications SUBSCRIBED")
                except Exception as e:
                    print(f"[REMOTE] command notify unavailable: {type(e).__name__}: {e}")
                    LOGGER.warning("remote command notify unavailable: %s", e)

                # The first Heltec that the collar actually accepts over BLE
                # becomes the LoRa network master. Secondary remotes discover
                # that master over LoRa and stop competing for the BLE slot.
                await remote_write(client, "ROLE|MASTER")

                # The Pi display signature includes remote.connected, so it will
                # show REMOTE MIRROR ACTIVE on its next refresh.
                await remote_write(client, "A|1")
                await remote_write(client, "C")

                while client.is_connected and not stop_event.is_set():
                    sig = remote_payload_signature()
                    now = time.monotonic()
                    changed = sig != remote.last_payload
                    send_due = now - remote.last_send >= REMOTE_MIN_SEND_INTERVAL
                    force_due = now - remote.last_send >= REMOTE_FORCE_SEND_INTERVAL

                    if (changed and send_due) or force_due:
                        await remote_send_state(client, sig)
                        remote.last_payload = sig
                        remote.last_send = now

                    await asyncio.sleep(0.25)

                # Best effort: tell the remote that the mirror is no longer live.
                if client.is_connected:
                    try:
                        await remote_write(client, "A|0")
                        await remote_write(client, "C")
                    except Exception:
                        pass

        except asyncio.CancelledError:
            raise
        except Exception as e:
            remote.error = str(e)
            print(f"[REMOTE] link error: {e}")
            LOGGER.warning("remote mirror link error: %s", e)
        finally:
            if remote.connected:
                log_event("remote_mirror_disconnected", address=remote.address)
            remote.connected = False
            remote.last_payload = None
            ble_pause_requested.clear()
            ble_scanner_paused.clear()
            print("[REMOTE] BLE adapter released; environmental scan may resume")

        await asyncio.sleep(REMOTE_RECONNECT_DELAY)


# ---------------------------------------------------------------------------
# ESP UART
# ---------------------------------------------------------------------------

def parse_esp_line(line: str):
    now = time.monotonic()
    esp.last_rx = now
    esp.connected = True

    p = line.split()
    if not p:
        return

    try:
        if p[0] == "READY":
            esp.ready_version = p[1] if len(p) > 1 else "?"
            print(f"[ESP] ready v{esp.ready_version}")
            return

        if p[0] == "HELLO" and len(p) >= 2 and p[1] == "ESP":
            # Discovery only. Do NOT declare the link established yet.
            esp.ready_version = p[2] if len(p) > 2 else esp.ready_version
            esp.connected = True
            if not esp.handshake:
                print(f"[LINK] ESP discovered, v{esp.ready_version or '?'}; sending HELLO")
            # The heartbeat task will continue sending HELLO PI until ACK.
            return

        if p[0] == "HELLO_ACK" and len(p) >= 2 and p[1] == "ESP":
            esp.ready_version = p[2] if len(p) > 2 else esp.ready_version
            if not esp.handshake:
                print(f"[LINK] handshake complete with ESP v{esp.ready_version or '?'}")
            esp.handshake = True
            esp.connected = True
            esp.last_pong = now
            return

        if p[0] == "PONG":
            # PONG only counts once a handshake already exists.
            if esp.handshake:
                esp.last_pong = now
                esp.connected = True
            return

        if p[0] == "SENS" and len(p) >= 4:
            esp.light_lux = safe_float(p[1])
            esp.temp_c = safe_float(p[2])
            esp.pressure_hpa = safe_float(p[3])
            return

        if p[0] == "IMU" and len(p) >= 9:
            esp.accel_g = (
                safe_float(p[1], 0.0),
                safe_float(p[2], 0.0),
                safe_float(p[3], 1.0),
            )
            esp.gyro_dps = (
                safe_float(p[4], 0.0),
                safe_float(p[5], 0.0),
                safe_float(p[6], 0.0),
            )
            esp.motion_score = safe_float(p[7], 0.0)
            esp.moving = safe_int(p[8], 0) != 0
            if len(p) >= 10:
                esp.motion_class = p[9].upper()
            else:
                # Backward compatibility with older ESP firmware.
                if esp.motion_score < 2.0:
                    esp.motion_class = "NORMAL"
                elif esp.motion_score < 4.0:
                    esp.motion_class = "NOTICEABLE"
                elif esp.motion_score < 8.0:
                    esp.motion_class = "STRONG"
                else:
                    esp.motion_class = "SUDDEN"
            return

        if p[0] == "EVENT" and len(p) >= 2:
            gesture = p[1].upper()
            if gesture in ("SHAKE", "FIDGET", "JOLT"):
                esp.last_gesture = gesture
                esp.last_gesture_time = now
                mind.observe_gesture(gesture)
                print(f"[MIND] gesture={gesture}")
            return

        if p[0] == "PULSE" and len(p) >= 6:
            ps = esp.pulse
            ps.present = True
            ps.contact = safe_int(p[1], 0) != 0
            raw_bpm = safe_float(p[2], 0.0)
            ps.bpm = raw_bpm if raw_bpm and raw_bpm > 0 else None
            ps.quality = max(0.0, min(100.0, safe_float(p[3], 0.0)))
            ps.ir = safe_int(p[4], 0)
            ps.red = safe_int(p[5], 0)
            ps.last_update = now
            return

        if p[0] == "GPS" and len(p) >= 8:
            g = esp.gps
            g.fix = safe_int(p[1], 0) != 0
            g.lat = safe_float(p[2])
            g.lon = safe_float(p[3])
            g.speed_kmh = safe_float(p[4], 0.0)
            g.satellites = safe_int(p[5], 0)
            g.hdop = safe_float(p[6])
            g.altitude_m = safe_float(p[7])
            g.last_update = now
            return

        if p[0] == "STATUS" and len(p) >= 3:
            if p[1] == "FAILSAFE":
                esp.failsafe = safe_int(p[2], 1) != 0
            elif p[1] == "STEALTH":
                esp.stealth = safe_int(p[2], 0) != 0
            return

        print(f"[ESP] {line}")

    except Exception as e:
        print(f"[ESP] parse error: {e}: {line}")



def sd_notify(message: str) -> bool:
    """Minimal systemd notify client; no python-systemd package required."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(message.encode("utf-8"))
        return True
    except Exception as e:
        print(f"[WDT] sd_notify error: {e}")
        return False


async def systemd_watchdog_task():
    # Tell Type=notify systemd that initialization reached the async runtime.
    sd_notify("READY=1\nSTATUS=CollarPet runtime active")
    print("[WDT] systemd watchdog active")
    while not stop_event.is_set():
        sd_notify("WATCHDOG=1")
        await asyncio.sleep(SYSTEMD_WATCHDOG_FEED_SECONDS)


async def esp_send(line: str):
    """Queue one complete ESP protocol line. This function never touches UART."""
    line = str(line).replace("\r", " ").replace("\n", " ").strip()
    if not line:
        return False

    # VU_OFF must cancel any stale unsent VU frame immediately.
    if line == "VU_OFF":
        esp_tx_latest.pop("VU", None)
        await esp_tx_queue.put(line)
    else:
        key = None
        for prefix in ESP_COALESCE_PREFIXES:
            if line.startswith(prefix):
                key = prefix.strip()
                break
        if key is not None:
            esp_tx_latest[key] = line
        else:
            await esp_tx_queue.put(line)

    esp_tx_wakeup.set()
    return True


def _esp_next_tx_line():
    """Return the next outbound line. Control FIFO has priority over state updates."""
    try:
        return esp_tx_queue.get_nowait(), True
    except asyncio.QueueEmpty:
        pass

    # Fixed order keeps behavior deterministic. Only the newest value of each
    # coalesced state is retained.
    for key in ("MOOD", "LEDLEVEL", "VU"):
        line = esp_tx_latest.pop(key, None)
        if line is not None:
            return line, False
    return None, False


async def esp_uart_task():
    """Single owner for ESP UART RX + TX. No other task may access esp_serial."""
    global esp_serial
    rxbuf = bytearray()

    while not stop_event.is_set():
        try:
            if esp_serial is None or not esp_serial.is_open:
                esp_serial = serial.Serial(ESP_PORT, ESP_BAUD, timeout=0, write_timeout=0.5)
                try:
                    esp_serial.reset_input_buffer()
                    esp_serial.reset_output_buffer()
                except Exception:
                    pass
                rxbuf.clear()
                print(f"[ESP] UART manager open {ESP_PORT} @ {ESP_BAUD}")
                await esp_send(f"HELLO PI {VERSION}")

            did_work = False

            # RX first so telemetry/PONG traffic cannot be starved by VU updates.
            waiting = esp_serial.in_waiting
            if waiting:
                raw = esp_serial.read(min(waiting, 4096))
                if raw:
                    did_work = True
                    rxbuf.extend(raw)
                    while b"\n" in rxbuf:
                        raw_line, _, rest = rxbuf.partition(b"\n")
                        rxbuf = bytearray(rest)
                        line = raw_line.decode("ascii", errors="ignore").strip("\r \t")
                        if line:
                            parse_esp_line(line)
                    if len(rxbuf) > 1024:
                        print("[ESP] RX line overflow; dropping partial line")
                        rxbuf.clear()

            # Exactly one physical writer, one whole line at a time.
            line, queued = _esp_next_tx_line()
            if line is not None:
                did_work = True
                try:
                    data = (line + "\n").encode("ascii", errors="strict")
                    esp_serial.write(data)
                    esp_serial.flush()
                finally:
                    if queued:
                        esp_tx_queue.task_done()

            if not did_work:
                esp_tx_wakeup.clear()
                try:
                    await asyncio.wait_for(esp_tx_wakeup.wait(), timeout=0.004)
                except asyncio.TimeoutError:
                    pass

        except Exception as e:
            esp.connected = False
            esp.handshake = False
            print(f"[ESP] UART manager error: {e}; retrying")
            try:
                if esp_serial:
                    esp_serial.close()
            except Exception:
                pass
            esp_serial = None
            rxbuf.clear()
            await asyncio.sleep(1.0)


async def esp_heartbeat_task():
    seq = 0
    last_hello = 0.0

    while not stop_event.is_set():
        now = time.monotonic()

        # Linux initiates discovery. While unpaired, retry only every 30 s.
        # Once paired, a 2 s ping/PONG heartbeat provides the mutual watchdog.
        if not esp.handshake:
            if now - last_hello >= ESP_HELLO_INTERVAL:
                await esp_send(f"HELLO PI {VERSION}")
                last_hello = now
        else:
            seq = (seq + 1) & 0x7FFFFFFF
            await esp_send(f"PING {seq}")

        # After the handshake, require actual PONG responses.
        # Sensor telemetry alone must never keep a dead control link "alive".
        if esp.handshake and esp.last_pong and now - esp.last_pong > ESP_WATCHDOG_SECONDS:
            print("[LINK] ESP PONG watchdog lost; dropping handshake")
            esp.handshake = False
            esp.connected = False
            esp.last_pong = 0.0

        await asyncio.sleep(ESP_PING_INTERVAL)


async def esp_device_sync_task():
    """
    The ESP owns persistent LED slots.

    Linux therefore sends stable device IDs + current RSSI, not LED positions.
    ESP decides where each comet lives.
    """
    previous_ids = set()

    while not stop_event.is_set():
        if not esp.handshake:
            await asyncio.sleep(ESP_DEVICE_SYNC_INTERVAL)
            continue

        items = visible_devices()
        current_ids = {d.dev_id for d in items}

        for d in items:
            typ = "BADGE" if d.is_badge else "FOX" if d.is_beacon else "UNKNOWN"
            await esp_send(
                f"DEVICE {d.dev_id:08X} {typ} {int(round(d.rssi))}"
            )

        # Explicit lost notification. ESP also has its own timeout/grace.
        for dev_id in previous_ids - current_ids:
            await esp_send(f"DEVICE_LOST {dev_id:08X}")

        previous_ids = current_ids
        await asyncio.sleep(ESP_DEVICE_SYNC_INTERVAL)


async def esp_state_task():
    """
    Send high-level pet state. This is intentionally sparse.
    """
    last_mood = None
    last_stealth = None
    last_led_percent = None

    while not stop_event.is_set():
        if not esp.handshake:
            last_mood = None
            last_stealth = None
            last_led_percent = None
            await asyncio.sleep(0.25)
            continue

        if world.mood != last_mood:
            await esp_send(f"MOOD {world.mood.upper()}")
            last_mood = world.mood

        if STEALTH != last_stealth:
            await esp_send(f"STEALTH {1 if STEALTH else 0}")
            last_stealth = STEALTH

        if COLLAR_LED_PERCENT != last_led_percent:
            await esp_send(f"LEDLEVEL {COLLAR_LED_PERCENT}")
            last_led_percent = COLLAR_LED_PERCENT

        await asyncio.sleep(0.25)



# ---------------------------------------------------------------------------
# I2S microphone / audio environment
# ---------------------------------------------------------------------------

_audio_env = deque(maxlen=max(32, int(3.0 * AUDIO_RATE / AUDIO_CHUNK_FRAMES)))
_audio_prev_spectrum = None
_audio_music_above_since = 0.0
_audio_music_below_since = 0.0


def _audio_norm_from_db(db, floor=-55.0, ceiling=-8.0):
    return max(0.0, min(1.0, (db - floor) / (ceiling - floor)))


def _audio_periodicity(values):
    if len(values) < 24:
        return 0.0
    vals = list(values)
    mean = sum(vals) / len(vals)
    x = [v - mean for v in vals]
    energy = sum(v * v for v in x)
    if energy <= 1e-12:
        return 0.0

    # Convert the desired tempo range to envelope-sample lags so this stays
    # correct if AUDIO_RATE or AUDIO_CHUNK_FRAMES changes.
    env_hz = AUDIO_RATE / max(1, AUDIO_CHUNK_FRAMES)
    min_lag = max(1, int(round(env_hz * 60.0 / 235.0)))
    max_lag = min(int(round(env_hz * 60.0 / 40.0)), len(x) // 2)
    if max_lag < min_lag:
        return 0.0

    best = 0.0
    for lag in range(min_lag, max_lag + 1):
        num = sum(x[i] * x[i - lag] for i in range(lag, len(x)))
        den_a = sum(x[i] * x[i] for i in range(lag, len(x)))
        den_b = sum(x[i - lag] * x[i - lag] for i in range(lag, len(x)))
        den = math.sqrt(max(1e-12, den_a * den_b))
        best = max(best, num / den)
    return max(0.0, min(1.0, best))


def _process_audio_chunk(raw):
    """Update audio features from one S32_LE ALSA chunk."""
    global _audio_prev_spectrum

    if np is None:
        audio.last_error = "numpy unavailable"
        return

    data = np.frombuffer(raw, dtype="<i4")
    if data.size < AUDIO_CHANNELS:
        return

    if AUDIO_CHANNELS > 1:
        frames = data[:data.size - (data.size % AUDIO_CHANNELS)].reshape(-1, AUDIO_CHANNELS)
        # INMP441 is normally strapped to one I2S channel. Pick whichever
        # channel actually contains the microphone instead of hard-coding L/R.
        channel_rms = np.sqrt(np.mean((frames.astype(np.float64) / 2147483648.0) ** 2, axis=0))
        samples = frames[:, int(np.argmax(channel_rms))].astype(np.float64) / 2147483648.0
    else:
        samples = data.astype(np.float64) / 2147483648.0

    if samples.size < 64:
        return

    samples = samples - float(np.mean(samples))
    rms = float(np.sqrt(np.mean(samples * samples)))
    peak = float(np.max(np.abs(samples)))
    dbfs = 20.0 * math.log10(max(rms, 1e-9))
    peak_db = 20.0 * math.log10(max(peak, 1e-9))
    level = _audio_norm_from_db(dbfs)
    peak_level = _audio_norm_from_db(peak_db, -50.0, -3.0)

    # FFT-derived texture. Music tends to be sustained and structured, while
    # silence is inactive and many noises are comparatively spectrally flat.
    n = min(1024, samples.size)
    x = samples[-n:]
    window = np.hanning(n)
    spec = np.abs(np.fft.rfft(x * window)) + 1e-12
    freqs = np.fft.rfftfreq(n, 1.0 / AUDIO_RATE)
    useful = (freqs >= 60.0) & (freqs <= min(7500.0, AUDIO_RATE * 0.48))
    s = spec[useful]

    if s.size:
        flatness = float(np.exp(np.mean(np.log(s))) / np.mean(s))
    else:
        flatness = 1.0

    norm_spec = spec / max(float(np.sum(spec)), 1e-12)
    flux = 0.0
    if _audio_prev_spectrum is not None and _audio_prev_spectrum.shape == norm_spec.shape:
        flux = float(np.sum(np.maximum(0.0, norm_spec - _audio_prev_spectrum)))
    _audio_prev_spectrum = norm_spec

    _audio_env.append(level)
    rhythmicity = _audio_periodicity(_audio_env)

    active = max(0.0, min(1.0, (dbfs + 52.0) / 28.0))
    tonal = max(0.0, min(1.0, 1.0 - flatness))
    sustained = min(1.0, sum(1 for v in _audio_env if v > 0.10) / max(1, len(_audio_env)) * 1.2)

    # First-generation music detector: intentionally conservative. This can be
    # swapped for an audio classifier later without changing the ESP protocol.
    # Acoustic/microphone-friendly detector. The old detector put too much
    # weight on rhythmicity, making sustained music struggle to ever reach the
    # 0.70 enter threshold. Activity + spectral structure + sustained energy are
    # now primary; rhythm remains useful evidence but is no longer mandatory.
    music_conf = max(0.0, min(1.0,
        0.33 * active +
        0.28 * tonal +
        0.15 * rhythmicity +
        0.24 * sustained
    ))

    speech_conf = max(0.0, min(1.0, active * (0.85 - 0.45 * rhythmicity)))
    sudden = peak_level > 0.82 and peak > max(0.035, rms * 4.5)

    audio.available = True
    audio.rms = rms
    audio.peak = peak
    audio.dbfs = dbfs
    audio.level = level
    audio.peak_level = peak_level
    audio.music_confidence = music_conf
    audio.speech_confidence = speech_conf
    audio.rhythmicity = rhythmicity
    audio.spectral_flatness = flatness
    audio.spectral_flux = flux
    audio.sudden = sudden

    # Cheap realtime path: only buffer audio with meaningful absolute energy.
    # Clear stale audio when capture is silent so an old song cannot linger in
    # the rolling fingerprint window.
    if dbfs >= SONG_AUDIO_GATE_DBFS:
        _song_buffer_add(samples)
    else:
        _song_buffer_clear()

    audio.last_update = time.monotonic()
    audio.last_error = ""


def _update_music_hysteresis(now):
    global _audio_music_above_since, _audio_music_below_since

    if audio.music_confidence >= AUDIO_MUSIC_ENTER_CONF:
        if not _audio_music_above_since:
            _audio_music_above_since = now
        _audio_music_below_since = 0.0
        if not audio.music and now - _audio_music_above_since >= AUDIO_MUSIC_ENTER_SECONDS:
            audio.music = True
            mind.arousal = min(1.0, mind.arousal + 0.12)
            mind.curiosity = min(1.0, mind.curiosity + 0.08)
            mind.last_stimulus = now
            log_event("audio_music", active=True, confidence=round(audio.music_confidence, 3))
    elif audio.music_confidence <= AUDIO_MUSIC_EXIT_CONF:
        if not _audio_music_below_since:
            _audio_music_below_since = now
        _audio_music_above_since = 0.0
        if audio.music and now - _audio_music_below_since >= AUDIO_MUSIC_EXIT_SECONDS:
            audio.music = False
            log_event("audio_music", active=False, confidence=round(audio.music_confidence, 3))
    else:
        _audio_music_above_since = 0.0
        _audio_music_below_since = 0.0


async def audio_task():
    """Capture the I2S microphone through ALSA and feed PetMind + ESP VU."""
    last_vu_send = 0.0
    last_music_state = False

    if np is None:
        audio.last_error = "numpy unavailable"
        LOGGER.warning("audio disabled: numpy unavailable")
        return

    while not stop_event.is_set():
        proc = None
        try:
            cmd = [
                "arecord", "-q",
                "-D", AUDIO_DEVICE,
                "-t", "raw",
                "-f", AUDIO_FORMAT,
                "-r", str(AUDIO_RATE),
                "-c", str(AUDIO_CHANNELS),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            audio.device = AUDIO_DEVICE
            print(f"[AUDIO] capture {AUDIO_DEVICE} {AUDIO_RATE}Hz {AUDIO_CHANNELS}ch {AUDIO_FORMAT}")

            bytes_per_frame = 4 * AUDIO_CHANNELS
            chunk_bytes = AUDIO_CHUNK_FRAMES * bytes_per_frame

            while not stop_event.is_set():
                raw = await proc.stdout.readexactly(chunk_bytes)
                _process_audio_chunk(raw)
                now = time.monotonic()
                _update_music_hysteresis(now)

                # Audio is also personality input now, not merely decoration.
                # The existing ONNX model consumes arousal/curiosity, so this
                # immediately influences neural inference without changing the
                # legacy model's fixed input tensor width.
                if audio.music:
                    mind.arousal += (0.62 - mind.arousal) * 0.004
                    mind.curiosity += (0.58 - mind.curiosity) * 0.002
                    mind.boredom = max(0.0, mind.boredom - 0.003)
                elif audio.level > 0.22:
                    mind.arousal = min(1.0, mind.arousal + 0.0015)

                if audio.sudden:
                    mind.arousal = min(1.0, mind.arousal + 0.035)
                    mind.last_stimulus = now

                if audio.music and now - last_vu_send >= AUDIO_VU_INTERVAL:
                    last_vu_send = now
                    await esp_send(
                        f"VU {int(round(audio.level * 255))} "
                        f"{int(round(audio.peak_level * 255))} "
                        f"{int(round(audio.music_confidence * 100))}"
                    )

                if last_music_state and not audio.music:
                    await esp_send("VU_OFF")
                last_music_state = audio.music

        except asyncio.IncompleteReadError:
            audio.last_error = "ALSA capture ended"
        except FileNotFoundError:
            audio.last_error = "arecord not installed"
        except Exception as e:
            audio.last_error = str(e)

        audio.available = False
        audio.music = False
        if last_music_state:
            await esp_send("VU_OFF")
        last_music_state = False

        if proc is not None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except Exception:
                pass

        if audio.last_error:
            LOGGER.warning("audio capture unavailable: %s", audio.last_error)
            print(f"[AUDIO] {audio.last_error}; retrying")
        await asyncio.sleep(3.0)


# ---------------------------------------------------------------------------
# Neural personality / expression layer
# ---------------------------------------------------------------------------

def neural_state_dict(now, items, gps_speed):
    b = ble_env.stats(now)

    # Slowly learn an approximate resting pulse baseline only from clean samples
    # while the collar is not moving strongly. It is a trend feature, not medical.
    if esp.pulse.valid and esp.motion_class in ("NORMAL", "NOTICEABLE"):
        mind.pulse_baseline = 0.995 * mind.pulse_baseline + 0.005 * float(esp.pulse.bpm)

    bpm_delta = 0.0
    if esp.pulse.valid and esp.pulse.bpm is not None:
        bpm_delta = float(esp.pulse.bpm) - mind.pulse_baseline

    primary = choose_primary(items)
    fox_near = bool(primary and primary.is_beacon and primary.rssi >= -55)
    quiet_seconds = max(0.0, now - mind.last_stimulus)

    return {
        "curiosity": mind.curiosity,
        "arousal": mind.arousal,
        "boredom": mind.boredom,
        "motion_score": esp.motion_score,
        "moving": esp.moving,
        "lux": esp.light_lux or 0.0,
        "pulse_valid": esp.pulse.valid,
        "bpm_delta": bpm_delta,
        "pulse_quality": esp.pulse.quality,
        "ble_density": b["density"],
        "ble_new_ratio": b["new_ratio"],
        "ble_strong_ratio": b["strong_ratio"],
        "ble_change": b["change"],
        "ef_badges": sum(1 for d in items if d.is_badge),
        "fox_near": fox_near,
        "gps_speed_kmh": gps_speed,
        "quiet_seconds": quiet_seconds,
        "dark": esp.light_lux is not None and esp.light_lux < DARK_LUX,
        "stealth": STEALTH,
        "audio_level": audio.level,
        "audio_peak": audio.peak_level,
        "audio_music": audio.music,
        "audio_music_confidence": audio.music_confidence,
        "audio_speech_confidence": audio.speech_confidence,
        "audio_rhythmicity": audio.rhythmicity,
        "audio_spectral_flatness": audio.spectral_flatness,
        "audio_spectral_flux": audio.spectral_flux,
        "audio_sudden": audio.sudden,
    }


def update_neural(now, items, gps_speed):
    if neural_brain is None:
        return
    if now - neural.last_infer < NEURAL_INTERVAL:
        return

    neural.last_infer = now
    try:
        result = neural_brain.infer(neural_state_dict(now, items, gps_speed))
        neural.available = True
        neural.expression = result.expression
        neural.confidence = result.confidence
        neural.top = sorted(result.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:4]
        neural.last_error = ""

        # Expression hysteresis. Strong special reactions can switch immediately;
        # softer personality changes get a short hold so the e-paper and LEDs do
        # not flap between equally plausible emotions.
        special = result.expression in ("startled", "foxfound", "overwhelmed")
        held_for = now - neural.held_since
        if special or held_for >= EXPRESSION_HOLD_SECONDS:
            if result.confidence >= NEURAL_MIN_CONFIDENCE and result.expression != neural.held_expression:
                neural.held_expression = result.expression
                neural.held_since = now
    except Exception as e:
        neural.available = False
        neural.last_error = str(e)
        LOGGER.warning("neural inference failed: %s", e)


# ---------------------------------------------------------------------------
# World state
# ---------------------------------------------------------------------------

def update_world():
    now = time.monotonic()
    items = visible_devices(now)
    primary = choose_primary(items)

    lux = esp.light_lux
    if lux is None:
        light_desc = "LIGHT ?"
    elif lux < DARK_LUX:
        light_desc = "DARK"
    elif lux < DIM_LUX:
        light_desc = "DIM"
    elif lux < BRIGHT_LUX:
        light_desc = "LIT"
    else:
        light_desc = "BRIGHT"

    g = esp.gps
    gps_recent = bool(g.last_update) and now - g.last_update < GPS_STALE_SECONDS
    gps_speed = g.speed_kmh if gps_recent and g.fix else 0.0

    # Motion score <= ~2 is ordinary collar movement, not "active".
    if gps_speed > 12:
        activity = "RIDING"
    elif gps_speed > GPS_STATIONARY_KMH:
        activity = "WALKING"
    elif esp.motion_class == "SUDDEN":
        activity = "JOLT"
    elif esp.motion_class == "STRONG":
        activity = "ACTIVE"
    elif esp.motion_class == "NOTICEABLE":
        activity = "NORMAL MOTION"
    else:
        activity = "RESTING"

    if gps_recent and g.fix and g.confidence == "GOOD" and lux is not None and lux > BRIGHT_LUX:
        environment = "OUTDOOR?"
    elif gps_recent and g.fix and g.confidence == "LOW":
        environment = "INDOOR?/WEAK GPS"
    elif not gps_recent or not g.fix:
        environment = "INDOOR?"
    else:
        environment = "UNKNOWN"

    if not gps_recent or not g.fix:
        gps_desc = "GPS NO FIX"
    else:
        gps_desc = f"GPS {g.confidence} {g.satellites}SAT"

    mind.update(len(items), esp.motion_class, esp.motion_score, lux)
    update_neural(now, items, gps_speed)

    mood = "idle"
    reason = "quiet"

    # Hard rules are intentionally few. Neural PetMind gets to own ordinary
    # personality, but stealth and a close fox beacon are authoritative.
    if STEALTH:
        mood = "sleepy"
        reason = "stealth"
    elif primary and primary.is_beacon and primary.rssi >= -55:
        mood = "foxfound"
        reason = "fox beacon"
    elif neural.available and neural.confidence >= NEURAL_MIN_CONFIDENCE:
        mood = neural.held_expression
        reason = f"neural {neural.confidence:.0%}"
    else:
        mind_mood, mind_reason = mind.suggested_mood()
        if mind_mood:
            mood = mind_mood
            reason = mind_reason
        elif lux is not None and lux < DARK_LUX:
            mood = "sleep"
            reason = "dark fallback"
        elif len(items) >= 4:
            mood = "curious"
            reason = "busy EF environment"

    world.mood = mood
    world.activity = activity
    world.environment = environment
    world.light_desc = light_desc
    world.gps_desc = gps_desc
    world.reason = reason


async def world_task():
    while not stop_event.is_set():
        update_world()
        await asyncio.sleep(0.25)


# ---------------------------------------------------------------------------
# E-paper
# ---------------------------------------------------------------------------

FONT = ImageFont.load_default()

def _load_font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return FONT

FONT_NOW_LABEL = _load_font(14, True)
FONT_NOW_TITLE = _load_font(20, True)
FONT_NOW_CONF = _load_font(15, True)


def _draw_centered(draw, y, text_value, font, fill):
    box = draw.textbbox((0, 0), text_value, font=font)
    width = box[2] - box[0]
    draw.text(((250 - width) // 2, y), text_value, font=font, fill=fill)


def _fit_song_title(title, max_chars=22):
    title = " ".join(str(title).split())
    if len(title) <= max_chars:
        return title
    return title[:max(1, max_chars - 1)].rstrip() + "…"


def build_now_playing_screen():
    """One bold inverted splash when a song is first recognized."""
    image = Image.new("1", (250, 122), 0)
    draw = ImageDraw.Draw(image)

    artist = _fit_song_title(_song_announce_artist, 24)
    title = _fit_song_title(_song_announce_title, 24)
    confidence = int(round(max(0.0, min(1.0, _song_announce_confidence)) * 100.0))

    _draw_centered(draw, 7, "I DETECTED THE SONG", FONT_NOW_LABEL, 1)
    draw.line((18, 27, 231, 27), fill=1, width=2)

    if artist:
        _draw_centered(draw, 36, artist, FONT_NOW_LABEL, 1)
        _draw_centered(draw, 57, title, FONT_NOW_TITLE, 1)
    else:
        _draw_centered(draw, 49, title, FONT_NOW_TITLE, 1)

    _draw_centered(draw, 94, f"{confidence}% MATCH", FONT_NOW_CONF, 1)
    return image



def load_wolf(mood):
    art_alias = {
        "suspicious": "annoyed", "sleepy": "sleep",
        "confused": "curious", "searching": "tracking", "content": "idle",
    }
    path = WOLF_FILES.get(mood, WOLF_FILES.get(art_alias.get(mood, "idle"), WOLF_FILES["idle"]))
    try:
        img = Image.open(path).convert("L")
        inv = ImageOps.invert(img)
        bbox = inv.getbbox()
        if bbox:
            img = img.crop(bbox)

        img.thumbnail((105, 105), Image.Resampling.LANCZOS)
        return img.point(lambda p: 255 if p > 170 else 0, mode="1")
    except Exception:
        return None


def _draw_monitor_icon(draw, x, y, active=True):
    # Tiny monitor icon: connection to the external CollarPet display network.
    draw.rectangle((x, y, x + 16, y + 10), outline=0)
    draw.line((x + 6, y + 11, x + 10, y + 11), fill=0)
    draw.line((x + 8, y + 10, x + 8, y + 13), fill=0)
    draw.line((x + 4, y + 13, x + 12, y + 13), fill=0)
    if not active:
        draw.line((x + 2, y + 2, x + 14, y + 8), fill=0)
        draw.line((x + 14, y + 2, x + 2, y + 8), fill=0)


def _draw_battery_icon(draw, x, y, percent=-1):
    draw.rectangle((x, y + 1, x + 17, y + 10), outline=0)
    draw.rectangle((x + 18, y + 4, x + 20, y + 7), fill=0)
    if percent is None or percent < 0:
        draw.line((x + 2, y + 3, x + 15, y + 9), fill=0)
        draw.line((x + 15, y + 3, x + 2, y + 9), fill=0)
        return
    pct = max(0, min(100, int(percent)))
    fill_w = int(round(13 * pct / 100.0))
    if fill_w > 0:
        draw.rectangle((x + 2, y + 3, x + 1 + fill_w, y + 8), fill=0)



_TAIL_ICON_POINTS = [(9, 0), (10, 0), (11, 0), (12, 0), (13, 0), (6, 1), (7, 1), (8, 1), (9, 1), (10, 1), (11, 1), (14, 1), (15, 1), (6, 2), (7, 2), (15, 2), (16, 2), (17, 2), (5, 3), (6, 3), (17, 3), (18, 3), (4, 4), (18, 4), (3, 5), (4, 5), (18, 5), (3, 6), (18, 6), (19, 6), (3, 7), (4, 7), (5, 7), (6, 7), (7, 7), (8, 7), (16, 7), (17, 7), (18, 7), (7, 8), (8, 8), (9, 8), (14, 8), (15, 8), (16, 8), (17, 8), (18, 8), (10, 9), (13, 9), (14, 9), (15, 9), (16, 9), (17, 9), (18, 9), (11, 10), (12, 10), (13, 10), (14, 10), (15, 10), (16, 10), (17, 10), (18, 10), (11, 11), (12, 11), (13, 11), (14, 11), (15, 11), (16, 11), (17, 11), (18, 11), (12, 12), (13, 12), (14, 12), (15, 12), (16, 12), (17, 12), (18, 12), (19, 12), (11, 13), (12, 13), (13, 13), (14, 13), (15, 13), (16, 13), (17, 13), (18, 13), (19, 13), (20, 13), (21, 13), (12, 14), (13, 14), (14, 14), (15, 14), (16, 14), (17, 14), (18, 14), (19, 14), (20, 14), (21, 14), (13, 15), (14, 15), (15, 15), (16, 15), (17, 15), (18, 15), (19, 15), (20, 15), (21, 15), (14, 16), (15, 16), (16, 16), (17, 16), (18, 16), (19, 16), (20, 16), (18, 17), (19, 17)]
_EARS_ICON_POINTS = [(2, 0), (3, 0), (21, 0), (22, 0), (2, 1), (3, 1), (4, 1), (20, 1), (21, 1), (22, 1), (1, 2), (2, 2), (3, 2), (4, 2), (5, 2), (19, 2), (20, 2), (21, 2), (22, 2), (23, 2), (1, 3), (2, 3), (4, 3), (5, 3), (6, 3), (18, 3), (19, 3), (20, 3), (22, 3), (23, 3), (1, 4), (5, 4), (6, 4), (7, 4), (17, 4), (18, 4), (19, 4), (23, 4), (0, 5), (1, 5), (6, 5), (7, 5), (16, 5), (17, 5), (18, 5), (23, 5), (24, 5), (0, 6), (1, 6), (6, 6), (7, 6), (8, 6), (16, 6), (17, 6), (18, 6), (23, 6), (24, 6), (0, 7), (1, 7), (7, 7), (8, 7), (15, 7), (16, 7), (17, 7), (23, 7), (24, 7), (0, 8), (1, 8), (5, 8), (7, 8), (8, 8), (9, 8), (15, 8), (16, 8), (17, 8), (19, 8), (23, 8), (24, 8), (0, 9), (1, 9), (5, 9), (6, 9), (7, 9), (8, 9), (9, 9), (15, 9), (16, 9), (17, 9), (18, 9), (19, 9), (23, 9), (24, 9), (0, 10), (1, 10), (6, 10), (7, 10), (8, 10), (9, 10), (15, 10), (16, 10), (17, 10), (18, 10), (23, 10), (24, 10), (0, 11), (1, 11), (4, 11), (5, 11), (6, 11), (7, 11), (8, 11), (9, 11), (10, 11), (14, 11), (15, 11), (16, 11), (17, 11), (18, 11), (19, 11), (20, 11), (23, 11), (24, 11), (0, 12), (1, 12), (4, 12), (5, 12), (6, 12), (7, 12), (8, 12), (9, 12), (10, 12), (14, 12), (15, 12), (16, 12), (17, 12), (18, 12), (19, 12), (20, 12), (23, 12), (24, 12), (1, 13), (5, 13), (6, 13), (7, 13), (8, 13), (9, 13), (15, 13), (16, 13), (17, 13), (18, 13), (19, 13), (23, 13), (1, 14), (4, 14), (5, 14), (6, 14), (18, 14), (19, 14), (20, 14), (22, 14), (23, 14), (2, 15), (4, 15), (5, 15), (19, 15), (20, 15), (22, 15), (2, 16), (3, 16), (4, 16), (20, 16), (21, 16), (22, 16), (3, 17), (21, 17)]

def _draw_icon_points(draw, x, y, points):
    for px, py in points:
        draw.point((x + px, y + py), fill=0)

def _draw_tail_icon(draw, x, y, active):
    if active: _draw_icon_points(draw, x, y, _TAIL_ICON_POINTS)

def _draw_ears_icon(draw, x, y, active):
    if active: _draw_icon_points(draw, x, y, _EARS_ICON_POINTS)

def _screen_line(value, chars=21):
    return _pet_text(value, chars)


def build_screen():
    if _song_announce_title and time.monotonic() < _song_announce_until:
        return build_now_playing_screen()

    image = Image.new("1", (250, 122), 255)
    draw = ImageDraw.Draw(image)

    # ------------------------------------------------------------------
    # Top status bar: compact system information only.
    # ------------------------------------------------------------------
    draw.text((4, 3), datetime.now().strftime("%H:%M"), font=FONT, fill=0)
    draw.text((76, 3), "MIRROR" if remote.connected else "NO MIRROR", font=FONT, fill=0)
    batt = current_pi_battery_percent()
    draw.text((194, 3), f"BAT {batt}%" if batt >= 0 else "BAT --", font=FONT, fill=0)
    draw.line((0, 17, 249, 17), fill=0)

    # ------------------------------------------------------------------
    # Portrait area: deliberately mirrors the remote layout.
    #
    #   portrait box = x 4..106, y 19..119
    #   wolf head    = centered in x 8..102, y 20..99
    #   tail icon    = lower-left under head
    #   ears icon    = lower-right under head
    #
    # No extra bottom bar or separator.
    # ------------------------------------------------------------------
    portrait_left = 4
    portrait_right = 106
    portrait_top = 19

    wolf = load_wolf(world.mood)
    if wolf:
        wolf = wolf.copy()
        # Bigger than v9.4.1 and matched to the remote's visual weight.
        wolf.thumbnail((96, 80), Image.Resampling.LANCZOS)
        wolf_x = portrait_left + (portrait_right - portrait_left - wolf.width) // 2
        wolf_y = portrait_top + max(0, (80 - wolf.height) // 2)
        image.paste(wolf, (wolf_x, wolf_y))

    # Gear icons sit directly under the lower corners of the head area.
    # Show ONLY when the actual BLE device is connected.
    if GEAR_MAIN_ENABLED and TAIL_ENABLED and TAIL_CONNECTED:
        _draw_tail_icon(draw, 14, 100, True)
    if GEAR_MAIN_ENABLED and EARS_ENABLED and EARS_CONNECTED:
        _draw_ears_icon(draw, 72, 100, True)

    # ------------------------------------------------------------------
    # Pet/status text area on the right.
    # ------------------------------------------------------------------
    x = 112
    y = 22
    line = 14

    mood = _screen_line(world.mood.upper(), 19)
    activity = _screen_line(str(world.activity).replace("_", " ").upper(), 19)
    reason = _screen_line(str(world.reason).replace("_", " "), 21)

    draw.text((x, y), mood or "IDLE", font=FONT, fill=0); y += line
    draw.text((x, y), activity or "RESTING", font=FONT, fill=0); y += line

    if reason and reason.lower() not in ("quiet", "none", "-"):
        draw.text((x, y), reason, font=FONT, fill=0); y += line

    song_recent = bool(audio.song_title and time.monotonic() - audio.song_last_seen <= 8.0)
    if song_recent:
        draw.text((x, y), _screen_line(audio.song_artist or "NOW PLAYING", 21), font=FONT, fill=0); y += line
        draw.text((x, y), _screen_line(audio.song_title, 21), font=FONT, fill=0); y += line
    else:
        items = visible_devices()
        primary = choose_primary(items)
        if primary and primary.is_beacon:
            draw.text((x, y), _screen_line(f"FOX: {primary.name}", 21), font=FONT, fill=0); y += line
        elif primary and primary.is_badge:
            draw.text((x, y), _screen_line(f"BADGE: {primary.name}", 21), font=FONT, fill=0); y += line

    # Only meaningful warnings get the last line.
    warning = ""
    if not (esp.connected and esp.handshake):
        warning = "ESP LINK LOST"
    elif STEALTH:
        warning = "STEALTH"

    if warning:
        draw.text((x, min(y, 100)), warning, font=FONT, fill=0)

    return image


def build_power_screen(action):
    action = action.upper()
    image = Image.new("1", (250, 122), 255)
    draw = ImageDraw.Draw(image)

    if action == "SHUTDOWN":
        path = SHUTDOWN_WOLF
    else:
        path = REBOOT_WOLF

    try:
        wolf = Image.open(path).convert("L")
        inv = ImageOps.invert(wolf)
        bbox = inv.getbbox()
        if bbox:
            wolf = wolf.crop(bbox)
        wolf.thumbnail((105, 105), Image.Resampling.LANCZOS)
        wolf = wolf.point(lambda q: 255 if q > 170 else 0, mode="1")
        y = max(0, (122 - wolf.height) // 2)
        image.paste(wolf, (3, y))
    except Exception as e:
        LOGGER.warning("power-screen wolf load failed: %s", e)

    x = 115
    draw.text((x, 8), "COLLARPET", font=FONT, fill=0)
    draw.line((x, 21, 247, 21), fill=0)

    if action == "SHUTDOWN":
        draw.text((x, 34), "GOOD NIGHT", font=FONT, fill=0)
        draw.text((x, 51), "Going to sleep...", font=FONT, fill=0)
        draw.text((x, 72), "Sensors: sleep", font=FONT, fill=0)
        draw.text((x, 86), "ESP: deep sleep", font=FONT, fill=0)
        draw.rectangle((x, 106, 249, 121), fill=0)
        draw.text((x + 3, 109), "POWER OFF", font=FONT, fill=1)
    else:
        draw.text((x, 34), "REBOOTING", font=FONT, fill=0)
        draw.text((x, 51), "Back soon...", font=FONT, fill=0)
        draw.text((x, 72), "Restarting brain", font=FONT, fill=0)
        draw.text((x, 86), "Please wait", font=FONT, fill=0)
        draw.rectangle((x, 106, 249, 121), fill=0)
        draw.text((x + 3, 109), "REBOOT", font=FONT, fill=1)

    return image


def restore_i2s_clk():
    """Raspberry-Pi-only compatibility shim.

    The old CollarPet build had to re-claim BCM GPIO18 after the Waveshare
    library touched it. That is not valid on the Orange Pi A733, so it is
    disabled unless COLLARPET_RPI_PINCTRL=1 is explicitly set.
    """
    if os.environ.get("COLLARPET_RPI_PINCTRL", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    try:
        result = subprocess.run(
            ["pinctrl", "set", "18", "a0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            timeout=2, check=False,
        )
        if result.returncode != 0:
            LOGGER.warning("could not restore Raspberry Pi GPIO18 PCM_CLK: %s", (result.stderr or "").strip())
            return False
        return True
    except Exception as e:
        LOGGER.warning("could not restore Raspberry Pi GPIO18 PCM_CLK: %s", e)
        return False


def _epd_finish(epd):
    try:
        epd.sleep()
    finally:
        restore_i2s_clk()


def epaper_power_screen(action):
    if not EPAPER_AVAILABLE:
        LOGGER.info("e-paper power screen skipped: display unavailable")
        return
    epd = EPD()
    image = build_power_screen(action)
    buf = epd.getbuffer(image)
    try:
        epd.init()
        restore_i2s_clk()
        epd.displayPartBaseImage(buf)
    finally:
        _epd_finish(epd)


def epaper_full_refresh():
    if not EPAPER_AVAILABLE:
        return
    epd = EPD()
    image = build_screen()
    buf = epd.getbuffer(image)
    try:
        epd.init()
        restore_i2s_clk()
        epd.displayPartBaseImage(buf)
    finally:
        _epd_finish(epd)


def epaper_partial_refresh():
    if not EPAPER_AVAILABLE:
        return
    epd = EPD()
    image = build_screen()
    buf = epd.getbuffer(image)
    try:
        epd.init()
        restore_i2s_clk()
        epd.displayPartial(buf)
    finally:
        _epd_finish(epd)


def display_signatures():
    items = visible_devices()
    primary = choose_primary(items)

    membership = tuple(sorted((d.dev_id, d.dev_type) for d in items))
    primary_id = None if not primary else (primary.dev_id, primary.dev_type)

    full_sig = (
        world.mood,
        world.activity,
        primary_id,
        membership,
        STEALTH,
        esp.connected,
        remote.connected,
        bool(_song_announce_title and time.monotonic() < _song_announce_until),
        _song_announce_artist if time.monotonic() < _song_announce_until else "",
        _song_announce_title if time.monotonic() < _song_announce_until else "",
    )

    # Keep normal sensor jitter from hammering the e-paper.
    detail_sig = (
        primary_id,
        None if not primary else round(primary.rssi / 5) * 5,
        world.light_desc,
        esp.gps.fix,
        esp.gps.confidence,
        # Quantize BPM so normal beat-to-beat jitter does not hammer the e-paper.
        esp.pulse.present,
        esp.pulse.contact,
        esp.pulse.valid,
        None if esp.pulse.bpm is None else round(esp.pulse.bpm / 5) * 5,
        round(esp.pulse.quality / 10) * 10,
        datetime.now().strftime("%H:%M"),
        audio.song_artist if time.monotonic() - audio.song_last_seen <= 8.0 else "",
        audio.song_title if time.monotonic() - audio.song_last_seen <= 8.0 else "",
        round(audio.song_confidence * 20) * 5 if audio.song_title else 0,
    )

    return full_sig, detail_sig


async def display_task():
    if not EPAPER_AVAILABLE:
        reason = f" ({EPAPER_IMPORT_ERROR})" if EPAPER_IMPORT_ERROR else ""
        print(f"[EPD] disabled/unavailable{reason}")
        while not stop_event.is_set():
            await asyncio.sleep(5.0)
        return

    """
    Keep the collar e-paper visually close to the live state.

    V8.4 could visibly lag because mood/RF changes requested a full refresh but
    then waited behind the full-refresh cooldown. V8.5 instead uses a partial
    refresh for live changes and reserves full refreshes for boot, explicit
    refresh requests, and periodic ghosting cleanup.
    """
    last_full_sig = None
    last_detail_sig = None
    last_full = 0.0
    last_partial = 0.0
    partial_count = 0
    first = True

    while not stop_event.is_set():
        try:
            # Shutdown/reboot owns the e-paper from the moment the terminal
            # screen is requested. Never overwrite it with a live-state frame.
            if epaper_terminal_lock.is_set():
                await asyncio.sleep(DISPLAY_LOOP_INTERVAL)
                continue

            now = time.monotonic()
            full_sig, detail_sig = display_signatures()
            forced = force_epaper_refresh.is_set()

            if forced:
                force_epaper_refresh.clear()

            cleanup_due = (
                not first
                and partial_count >= MAX_PARTIAL_REFRESHES
                and now - last_full >= FULL_REFRESH_MIN_INTERVAL
            )

            if first or forced or cleanup_due:
                async with epaper_lock:
                    await asyncio.to_thread(epaper_full_refresh)

                first = False
                last_full_sig = full_sig
                last_detail_sig = detail_sig
                last_full = time.monotonic()
                last_partial = last_full
                partial_count = 0

            else:
                live_changed = (
                    full_sig != last_full_sig
                    or detail_sig != last_detail_sig
                )

                if live_changed and now - last_partial >= PARTIAL_REFRESH_INTERVAL:
                    async with epaper_lock:
                        await asyncio.to_thread(epaper_partial_refresh)

                    # A partial refresh displayed the complete current frame, so
                    # both signatures now match what is actually on the panel.
                    last_full_sig = full_sig
                    last_detail_sig = detail_sig
                    last_partial = time.monotonic()
                    partial_count += 1

        except Exception as e:
            print(f"[EPD] refresh error: {e}")
            await asyncio.sleep(2.0)

        await asyncio.sleep(DISPLAY_LOOP_INTERVAL)



def write_status_snapshot():
    items = visible_devices()
    primary = choose_primary(items)
    g = esp.gps

    payload = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "version": VERSION,
        "pet": {
            "mood": world.mood,
            "activity": world.activity,
            "reason": world.reason,
            "curiosity": round(mind.curiosity, 3),
            "arousal": round(mind.arousal, 3),
            "boredom": round(mind.boredom, 3),
            "known_devices": len(mind.seen_devices),
        },
        "neural": {
            "available": neural.available,
            "expression": neural.expression,
            "held_expression": neural.held_expression,
            "confidence": round(neural.confidence, 3),
            "top": [[k, round(v, 3)] for k, v in neural.top],
            "error": neural.last_error or None,
        },
        "ble_environment": ble_env.stats(),
        "esp": {
            "connected": esp.connected,
            "handshake": esp.handshake,
            "failsafe": esp.failsafe,
            "stealth": esp.stealth,
            "version": esp.ready_version,
        },
        "remote_mirror": {
            "discovered": remote.discovered,
            "connected": remote.connected,
            "address": remote.address or None,
            "rssi": remote.rssi if remote.discovered else None,
            "last_seen_age_s": None if not remote.last_seen else round(time.monotonic() - remote.last_seen, 1),
            "error": remote.error or None,
        },
        "environment": {
            "lux": esp.light_lux,
            "temperature_c": esp.temp_c,
            "pressure_hpa": esp.pressure_hpa,
        },
        "audio": {
            "available": audio.available,
            "device": audio.device,
            "dbfs": round(audio.dbfs, 1),
            "level": round(audio.level, 3),
            "peak": round(audio.peak_level, 3),
            "music": audio.music,
            "music_confidence": round(audio.music_confidence, 3),
            "speech_confidence": round(audio.speech_confidence, 3),
            "rhythmicity": round(audio.rhythmicity, 3),
            "spectral_flatness": round(audio.spectral_flatness, 3),
            "spectral_flux": round(audio.spectral_flux, 4),
            "sudden": audio.sudden,
            "error": audio.last_error or None,
        },
        "motion": {
            "moving": esp.moving,
            "score": round(esp.motion_score, 3),
            "class": esp.motion_class,
            "last_gesture": esp.last_gesture or None,
        },
        "pulse": {
            "present": esp.pulse.present,
            "contact": esp.pulse.contact,
            "bpm": None if esp.pulse.bpm is None else round(esp.pulse.bpm, 1),
            "quality": round(esp.pulse.quality, 1),
            "valid": esp.pulse.valid,
            "ir": esp.pulse.ir,
            "red": esp.pulse.red,
        },
        "gps": {
            "fix": g.fix,
            "confidence": g.confidence,
            "lat": g.lat,
            "lon": g.lon,
            "speed_kmh": g.speed_kmh,
            "satellites": g.satellites,
            "hdop": g.hdop,
            "altitude_m": g.altitude_m,
        },
        "rf": {
            "visible_count": len(items),
            "primary": None if primary is None else {
                "id": f"{primary.dev_id:08X}",
                "name": primary.name,
                "type": "BADGE" if primary.is_badge else "FOX" if primary.is_beacon else "?",
                "rssi": round(primary.rssi, 1),
            },
            "visible": [
                {
                    "id": f"{d.dev_id:08X}",
                    "name": d.name,
                    "type": "BADGE" if d.is_badge else "FOX" if d.is_beacon else "?",
                    "rssi": round(d.rssi, 1),
                }
                for d in items[:12]
            ],
        },
    }

    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(STATUS_FILE)


async def memory_task():
    last_memory_save = 0.0

    while not stop_event.is_set():
        now = time.monotonic()

        try:
            write_status_snapshot()
        except Exception as e:
            LOGGER.warning("status snapshot failed: %s", e)

        if now - last_memory_save >= 60.0:
            last_memory_save = now
            try:
                mind.save()
            except Exception as e:
                LOGGER.warning("memory save failed: %s", e)

        await asyncio.sleep(5.0)


# ---------------------------------------------------------------------------
# Console diagnostics
# ---------------------------------------------------------------------------

async def console_task():
    while not stop_event.is_set():
        await asyncio.sleep(2.0)

        items = visible_devices()
        primary = choose_primary(items)
        g = esp.gps

        print("\n" + "=" * 78)
        print(
            f"V{VERSION} {datetime.now().strftime('%H:%M:%S')} "
            f"mood={world.mood} activity={world.activity} "
            f"reason={world.reason}"
        )
        print(
            f"MIND curiosity={mind.curiosity:.2f} arousal={mind.arousal:.2f} "
            f"boredom={mind.boredom:.2f} known={len(mind.seen_devices)}"
        )
        b = ble_env.stats()
        if neural.available:
            top = " ".join(f"{k}={v:.2f}" for k, v in neural.top[:3])
            print(
                f"NEURAL expr={neural.expression} held={neural.held_expression} "
                f"conf={neural.confidence:.0%} {top}"
            )
        else:
            print(f"NEURAL unavailable: {neural.last_error}")
        print(
            f"BLEENV density={b['density']} new={b['new_ratio']:.2f} "
            f"strong={b['strong_ratio']:.2f} change={b['change']:+.2f}"
        )
        remote_age = None if not remote.last_seen else time.monotonic() - remote.last_seen
        remote_age_text = "never" if remote_age is None else f"{remote_age:.1f}s"
        print(
            f"REMOTE connected={remote.connected} discovered={remote.discovered} "
            f"rssi={remote.rssi if remote.discovered else '?'} age={remote_age_text} "
            f"error={remote.error or '-'}"
        )

        pong_age = (
            time.monotonic() - esp.last_pong
            if esp.last_pong
            else None
        )
        pong_text = "never" if pong_age is None else f"{pong_age:.1f}s"

        print(
            f"ESP connected={esp.connected} handshake={esp.handshake} "
            f"v={esp.ready_version or '?'} failsafe={esp.failsafe} "
            f"stealth={esp.stealth} pong_age={pong_text}"
        )

        print(
            f"SENS lux={esp.light_lux} temp={esp.temp_c}C "
            f"pressure={esp.pressure_hpa}hPa"
        )

        if esp.pulse.present:
            bpm_text = "?" if esp.pulse.bpm is None else f"{esp.pulse.bpm:.1f}"
            print(
                f"PULSE contact={esp.pulse.contact} bpm={bpm_text} "
                f"quality={esp.pulse.quality:.0f}% valid={esp.pulse.valid} "
                f"IR={esp.pulse.ir} RED={esp.pulse.red}"
            )
        else:
            print("PULSE sensor not reported")

        if audio.available:
            print(
                f"AUDIO {audio.dbfs:.1f}dBFS level={audio.level:.2f} "
                f"peak={audio.peak_level:.2f} music={audio.music} "
                f"music_conf={audio.music_confidence:.2f} rhythm={audio.rhythmicity:.2f}"
            )
        elif audio.last_error:
            print(f"AUDIO unavailable: {audio.last_error}")

        ax, ay, az = esp.accel_g
        gx, gy, gz = esp.gyro_dps
        print(
            f"IMU A=({ax:+.2f},{ay:+.2f},{az:+.2f})g "
            f"G=({gx:+.1f},{gy:+.1f},{gz:+.1f})dps "
            f"score={esp.motion_score:.2f} class={esp.motion_class} moving={esp.moving}"
        )

        if g.fix:
            print(
                f"GPS fix={g.fix} conf={g.confidence} "
                f"sat={g.satellites} hdop={g.hdop} "
                f"speed={g.speed_kmh:.1f}km/h "
                f"pos={g.lat},{g.lon}"
            )
        else:
            print("GPS NO FIX")

        for d in items:
            mark = ">" if primary and d.dev_id == primary.dev_id else " "
            typ = "BADGE" if d.is_badge else "FOX" if d.is_beacon else "?"
            print(
                f"{mark} {typ:5s} {d.name[:24]:24s} "
                f"{d.rssi:6.1f}dBm id={d.dev_id:08X} "
                f"raw={d.raw_payload.hex()}"
            )


# ---------------------------------------------------------------------------
# Main / shutdown
# ---------------------------------------------------------------------------

def request_stop():
    if not stop_event.is_set():
        print(f"\n[SYS] stopping CollarPet V{VERSION}...")
        stop_event.set()


async def main():
    if EPAPER_AVAILABLE:
        print("[EPD] driver available")
    else:
        reason = f": {EPAPER_IMPORT_ERROR}" if EPAPER_IMPORT_ERROR else ""
        print(f"[EPD] disabled/unavailable{reason}")

    # Raspberry-Pi-only compatibility path; normally OFF on Orange Pi.
    if restore_i2s_clk():
        print("[AUDIO] Raspberry Pi GPIO18 -> PCM_CLK")

    ready_marker = STATE_DIR / "startup_ready"
    if ready_marker.exists():
        try:
            ready_marker.unlink()
        except Exception:
            pass

    print(f"CollarPet Linux Brain V{VERSION}")
    print(
        f"PetMind loaded: curiosity={mind.curiosity:.2f} "
        f"known_devices={len(mind.seen_devices)}"
    )
    LOGGER.info(
        "CollarPet V%s starting; PetMind curiosity=%.3f known=%d",
        VERSION, mind.curiosity, len(mind.seen_devices)
    )
    log_event(
        "runtime_start",
        version=VERSION,
        curiosity=round(mind.curiosity, 3),
        known_devices=len(mind.seen_devices),
    )
    print(f"ESP UART: {ESP_PORT} @ {ESP_BAUD}")
    print(f"Display priority: {DISPLAY_PRIORITY}")
    print(
        f"Neural PetMind: {'READY' if neural.available else 'OFF'} "
        f"model={NEURAL_MODEL_FILE.name}"
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    _song_worker_start()

    tasks = [
        asyncio.create_task(systemd_watchdog_task(), name="systemd-watchdog"),
        asyncio.create_task(esp_uart_task(), name="esp-uart-manager"),
        asyncio.create_task(esp_heartbeat_task(), name="esp-heartbeat"),
        asyncio.create_task(esp_device_sync_task(), name="esp-rf-sync"),
        asyncio.create_task(esp_state_task(), name="esp-state"),
        asyncio.create_task(ble_task(), name="ble"),
        asyncio.create_task(audio_task(), name="audio"),
        asyncio.create_task(song_match_task(), name="song-match"),
        asyncio.create_task(remote_mirror_task(), name="remote-mirror"),
        asyncio.create_task(gear_manager.run(), name="tail-gear"),
        asyncio.create_task(ear_manager.run(), name="ear-gear"),
        asyncio.create_task(world_task(), name="world"),
        asyncio.create_task(memory_task(), name="memory"),
        asyncio.create_task(display_task(), name="epaper"),
        asyncio.create_task(console_task(), name="console"),
    ]

    try:
        await stop_event.wait()
    finally:
        # Tell ESP to become quiet before tearing down the link.
        try:
            await esp_send("VU_OFF")
            await esp_send("STEALTH 1")
            await esp_send("CLEAR_DEVICES")
            await asyncio.sleep(0.15)
        except Exception:
            pass

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        _song_worker_stop()

        global esp_serial
        if esp_serial:
            try:
                esp_serial.close()
            except Exception:
                pass

        try:
            mind.save()
            log_event("runtime_stop", version=VERSION)
            LOGGER.info("CollarPet stopped")
        except Exception:
            pass

        print("[SYS] stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
