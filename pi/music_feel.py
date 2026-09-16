#!/usr/bin/env python3
from collections import deque
import math
import time

class MusicFeelTracker:
    def __init__(self):
        self.samples = deque(maxlen=30)
        self.peaks = deque(maxlen=20)
        self.smooth = 0.0
        self.last_peak_t = 0.0
        self.last_state = "CALM"
        self.state_since = time.monotonic()

    def update(self, level, peak, music_conf=0.0, speech_conf=0.0):
        now = time.monotonic()
        raw = max(float(level), float(peak) * 0.85)
        raw = max(0.0, min(1.0, raw))
        self.smooth = 0.72 * self.smooth + 0.28 * raw

        prev = self.samples[-1][1] if self.samples else self.smooth
        delta = max(0.0, self.smooth - prev)
        self.samples.append((now, self.smooth, delta))

        recent = [x for x in self.samples if now - x[0] <= 4.0]
        vals = [x[1] for x in recent] or [0.0]
        deltas = [x[2] for x in recent] or [0.0]

        mean = sum(vals) / len(vals)
        variance = sum((x - mean) ** 2 for x in vals) / len(vals)
        stdev = math.sqrt(max(0.0, variance))
        transient = max(deltas[-3:]) if deltas else 0.0

        peak_event = False
        local_threshold = max(0.08, mean + max(0.035, stdev * 0.55))
        if self.smooth >= local_threshold and delta >= 0.035 and now - self.last_peak_t >= 0.28:
            self.last_peak_t = now
            self.peaks.append(now)
            peak_event = True

        active_peaks = [t for t in self.peaks if now - t <= 4.0]
        pulse_rate = len(active_peaks) / 4.0

        if mean < 0.10 and stdev < 0.045:
            candidate = "CALM"
        elif mean >= 0.42 and (stdev >= 0.11 or pulse_rate >= 1.5):
            candidate = "ENERGETIC"
        elif transient >= 0.14 or (peak_event and self.smooth >= 0.55):
            candidate = "PUNCHY"
        elif pulse_rate >= 1.0 and stdev >= 0.055:
            candidate = "BOUNCY"
        else:
            candidate = "FLOWING"

        if float(speech_conf) > float(music_conf) + 0.18 and candidate in ("PUNCHY", "BOUNCY"):
            candidate = "FLOWING"

        if candidate != self.last_state and now - self.state_since >= 1.0:
            self.last_state = candidate
            self.state_since = now

        return {
            "state": self.last_state,
            "level": self.smooth,
            "mean": mean,
            "stdev": stdev,
            "transient": transient,
            "pulse_rate": pulse_rate,
            "peak_event": peak_event,
        }

music_feel = MusicFeelTracker()
