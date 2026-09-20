#!/usr/bin/env python3
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict
import math
import numpy as np
import onnxruntime as ort

MODEL_VERSION = "0.1"
FEATURES = [
    "curiosity","arousal","boredom","motion_score","moving","lux","pulse_valid",
    "bpm_delta","pulse_quality","ble_density","ble_new_ratio","ble_strong_ratio",
    "ble_change","ef_badges","fox_near","gps_speed_kmh","quiet_seconds","dark","stealth",
]
EXPRESSIONS = [
    "idle","curious","listening","suspicious","happy","annoyed","sleepy","startled",
    "tracking","social","overwhelmed","confused","smug","searching","content","foxfound",
]

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
    ], dtype=np.float32)

class NeuralPetMind:
    def __init__(self, model_path: str | Path, history: int = 16):
        self.model_path = str(model_path)
        self.history = max(4, int(history))
        self.frames = deque(maxlen=self.history)
        self.session = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        self.reset()

    def reset(self):
        self.frames.clear()
        neutral = make_feature_vector({"curiosity": 0.30, "arousal": 0.15, "boredom": 0.0, "lux": 100.0})
        for _ in range(self.history):
            self.frames.append(neutral.copy())

    def infer(self, state: Dict) -> BrainResult:
        self.frames.append(make_feature_vector(state))
        x = np.stack(self.frames, axis=0)[None, :, :]
        logits = self.session.run([self.output_name], {self.input_name: x})[0][0].astype(np.float64)
        logits -= logits.max()
        p = np.exp(logits)
        p /= p.sum()
        index = int(np.argmax(p))
        return BrainResult(EXPRESSIONS[index], float(p[index]), {name: float(p[i]) for i, name in enumerate(EXPRESSIONS)})

if __name__ == "__main__":
    import argparse, random, time
    ap = argparse.ArgumentParser()
    ap.add_argument("model", nargs="?", default="petmind_v0_1.onnx")
    args = ap.parse_args()
    brain = NeuralPetMind(args.model)
    print(f"CollarPet Neural Brain v{MODEL_VERSION}; Ctrl+C to stop")
    try:
        while True:
            t = time.monotonic()
            state = {
                "curiosity": 0.35 + 0.25 * (0.5 + 0.5 * math.sin(t / 9)),
                "arousal": 0.18 + 0.15 * (0.5 + 0.5 * math.sin(t / 5)),
                "boredom": 0.3,
                "motion_score": 0.7 + random.random() * 0.4,
                "moving": False,
                "lux": 80,
                "ble_density": 8 + int(20 * (0.5 + 0.5 * math.sin(t / 12))),
                "ble_new_ratio": random.random() * 0.12,
                "ble_strong_ratio": random.random() * 0.2,
                "ble_change": random.uniform(-0.1, 0.1),
                "ef_badges": 0,
                "fox_near": False,
                "quiet_seconds": 20,
            }
            r = brain.infer(state)
            top = sorted(r.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:4]
            print(f"{r.expression:12s} {r.confidence:5.1%}  " + " ".join(f"{k}={v:.2f}" for k,v in top))
            time.sleep(1)
    except KeyboardInterrupt:
        print()
