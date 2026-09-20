#!/usr/bin/env python3
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict
import math
import numpy as np
import onnxruntime as ort
from petmind_schema_v0_3 import MODEL_VERSION, FEATURES, EXPRESSIONS

@dataclass
class BrainResult:
    expression: str
    confidence: float
    probabilities: Dict[str, float]

def _clip(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(v)))

def make_feature_vector(state: Dict) -> np.ndarray:
    lux = max(0.0, float(state.get("lux", 0.0) or 0.0))
    pulse_valid = 1.0 if state.get("pulse_valid", False) else 0.0
    return np.asarray([
        _clip(state.get("curiosity", 0.30)),
        _clip(state.get("arousal", 0.15)),
        _clip(state.get("boredom", 0.0)),
        _clip(float(state.get("motion_score", 0.0)) / 10.0),
        1.0 if state.get("moving", False) else 0.0,
        _clip(math.log10(lux + 1.0) / 4.0),
        pulse_valid,
        _clip((float(state.get("bpm_delta", 0.0)) + 50.0) / 100.0),
        _clip(float(state.get("pulse_quality", 0.0)) / 100.0),
        _clip(float(state.get("ble_density", 0.0)) / 80.0),
        _clip(state.get("ble_new_ratio", 0.0)),
        _clip(state.get("ble_strong_ratio", 0.0)),
        _clip((float(state.get("ble_change", 0.0)) + 1.0) / 2.0),
        _clip(float(state.get("ef_badges", 0.0)) / 12.0),
        1.0 if state.get("fox_near", False) else 0.0,
        _clip(float(state.get("gps_speed_kmh", 0.0)) / 35.0),
        _clip(float(state.get("quiet_seconds", 0.0)) / 180.0),
        1.0 if state.get("dark", False) else 0.0,
        1.0 if state.get("stealth", False) else 0.0,
        _clip(state.get("audio_level", 0.0)),
        _clip(state.get("audio_peak", 0.0)),
        1.0 if state.get("audio_music", False) else 0.0,
        _clip(state.get("audio_music_confidence", 0.0)),
        _clip(state.get("audio_speech_confidence", 0.0)),
        _clip(state.get("audio_rhythmicity", 0.0)),
        _clip(state.get("audio_spectral_flatness", 0.0)),
        _clip(state.get("audio_spectral_flux", 0.0)),
        1.0 if state.get("audio_sudden", False) else 0.0,
    ], dtype=np.float32)

class NeuralPetMind:
    def __init__(self, model_path: str | Path, history: int = 16):
        self.model_path = str(model_path)
        self.history = max(4, int(history))
        self.frames = deque(maxlen=self.history)
        self.session = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        shape = self.session.get_inputs()[0].shape
        if len(shape) != 3:
            raise RuntimeError(f"PetMind input must be [batch,history,features], got {shape}")
        if isinstance(shape[1], int) and shape[1] != self.history:
            raise RuntimeError(f"PetMind history mismatch: model={shape[1]} runtime={self.history}")
        if isinstance(shape[2], int) and shape[2] != len(FEATURES):
            raise RuntimeError(f"PetMind feature mismatch: model={shape[2]} runtime={len(FEATURES)}")
        self.reset()

    def reset(self):
        self.frames.clear()
        neutral = make_feature_vector({"curiosity":0.30,"arousal":0.15,"boredom":0.0,"lux":100.0})
        for _ in range(self.history):
            self.frames.append(neutral.copy())

    def infer(self, state: Dict) -> BrainResult:
        self.frames.append(make_feature_vector(state))
        x = np.stack(self.frames, axis=0)[None,:,:]
        logits = self.session.run([self.output_name], {self.input_name:x})[0][0].astype(np.float64)
        if logits.shape != (len(EXPRESSIONS),):
            raise RuntimeError(f"PetMind output mismatch: expected {len(EXPRESSIONS)}, got {logits.shape}")
        logits -= logits.max()
        p = np.exp(logits); p /= p.sum()
        idx = int(np.argmax(p))
        return BrainResult(EXPRESSIONS[idx], float(p[idx]), {name:float(p[i]) for i,name in enumerate(EXPRESSIONS)})
