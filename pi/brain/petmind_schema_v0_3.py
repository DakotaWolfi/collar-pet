#!/usr/bin/env python3
MODEL_VERSION = "0.3-audio"

FEATURES = [
    "curiosity","arousal","boredom","motion_score","moving","lux","pulse_valid",
    "bpm_delta","pulse_quality","ble_density","ble_new_ratio","ble_strong_ratio",
    "ble_change","ef_badges","fox_near","gps_speed_kmh","quiet_seconds","dark","stealth",
    "audio_level","audio_peak","audio_music","audio_music_confidence",
    "audio_speech_confidence","audio_rhythmicity","audio_spectral_flatness",
    "audio_spectral_flux","audio_sudden",
]

EXPRESSIONS = [
    "idle","curious","listening","suspicious","happy","annoyed","sleepy","startled",
    "tracking","social","overwhelmed","confused","smug","searching","content","foxfound",
]
