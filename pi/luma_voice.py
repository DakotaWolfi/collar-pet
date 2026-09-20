#!/usr/bin/env python3
import json, queue, re, threading, time
from pathlib import Path

try:
    import numpy as np
except Exception:
    np = None

try:
    from vosk import Model, KaldiRecognizer, SetLogLevel
except Exception as e:
    Model = KaldiRecognizer = None
    SetLogLevel = None
    VOSK_IMPORT_ERROR = e
else:
    VOSK_IMPORT_ERROR = None


class LumaVoice:
    SAMPLE_RATE = 16000
    COMMAND_WINDOW_SECONDS = 4.0

    WAKE_ALIASES = ("luma", "looma", "lumen", "loom a")

    # Canonical commands understood by CollarPet.
    COMMANDS = (
        "tail happy", "tail home", "tail shy",
        "ears center", "ears perk", "ears relax", "ears left", "ears right",
        "ears twitch", "ears wiggle", "ears listen", "ears tilt", "ears stop",
        "lights on", "lights off",
        "stealth on", "stealth off",
        "attention", "wake up", "calm down", "status",
        "haptic feedback on", "haptic feedback off",
        "ears react on", "ears react off", "ears music on", "ears music off",
        "connect tail", "connect ears", "connect gear",
        "gear vu on", "gear vu off",
    )

    # Acoustic / word-order variants that Vosk may produce.
    ALIASES = {
        "happy tail": "tail happy",
        "home tail": "tail home",
        "shy tail": "tail shy",

        "center ears": "ears center",
        "perk ears": "ears perk",
        "perky ears": "ears perk",
        "relax ears": "ears relax",
        "left ears": "ears left",
        "right ears": "ears right",
        "twitch ears": "ears twitch",
        "wiggle ears": "ears wiggle",
        "listen ears": "ears listen",
        "tilt ears": "ears tilt",
        "stop ears": "ears stop",

        "light on": "lights on",
        "light off": "lights off",

        "haptic on": "haptic feedback on",
        "haptic off": "haptic feedback off",
        "feedback on": "haptic feedback on",
        "feedback off": "haptic feedback off",

        "ear react on": "ears react on",
        "ear react off": "ears react off",
        "ears reaction on": "ears react on",
        "ears reaction off": "ears react off",
        "ear music on": "ears music on",
        "ear music off": "ears music off",
        "music ears on": "ears music on",
        "music ears off": "ears music off",
        "tail connect": "connect tail",
        "connect ear": "connect ears",
        "ear connect": "connect ears",
        "ears connect": "connect ears",
        "gear connect": "connect gear",
        "active gear on": "gear vu on",
        "active gear off": "gear vu off",
        "vu gear on": "gear vu on",
        "vu gear off": "gear vu off",
        "music gear on": "gear vu on",
        "music gear off": "gear vu off",
    }

    def __init__(self, model_path, callback, wake_callback=None, min_confidence=0.55, log=None):
        self.model_path = Path(model_path)
        self.callback = callback
        self.wake_callback = wake_callback
        self.min_confidence = float(min_confidence)
        self.log = log or print

        self._q = queue.Queue(maxsize=48)
        self._stop = threading.Event()
        self._thread = None

        self.ready = False
        self.last_error = ""
        self.last_text = ""
        self.last_confidence = 0.0
        self.last_command_time = 0.0
        self.last_wake_time = 0.0
        self.command_window_until = 0.0
        self._wake_latched = False
        self.dropped_chunks = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="luma-vosk", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)
        self.ready = False

    def feed_float(self, samples, input_rate):
        if not self.ready or np is None or samples is None or len(samples) < 1:
            return

        x = np.asarray(samples, dtype=np.float32)
        rate = int(input_rate)

        if rate == self.SAMPLE_RATE:
            y = x
        elif rate > 0 and rate % self.SAMPLE_RATE == 0:
            y = x[:: rate // self.SAMPLE_RATE]
        else:
            out_len = max(1, int(round(len(x) * self.SAMPLE_RATE / max(1, rate))))
            src = np.arange(len(x), dtype=np.float32)
            dst = np.linspace(0, max(0, len(x)-1), out_len, dtype=np.float32)
            y = np.interp(dst, src, x).astype(np.float32)

        pcm = (np.clip(y, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

        try:
            self._q.put_nowait(pcm)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(pcm)
            except queue.Full:
                self.dropped_chunks += 1

    @staticmethod
    def _norm(text):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())).strip()

    def _canonical_command(self, text):
        t = self._norm(text)
        if t in self.COMMANDS:
            return t
        return self.ALIASES.get(t)

    def _strip_wake(self, text):
        t = self._norm(text)
        for wake in self.WAKE_ALIASES:
            w = self._norm(wake)
            if t == w:
                return True, ""
            if t.startswith(w + " "):
                return True, t[len(w):].strip()
        return False, t

    @staticmethod
    def _confidence(result):
        words = result.get("result") or []
        vals = [float(x.get("conf", 0.0)) for x in words if "conf" in x]
        return sum(vals) / len(vals) if vals else 1.0

    def _open_command_window(self):
        now = time.monotonic()
        self.command_window_until = now + self.COMMAND_WINDOW_SECONDS
        if now - self.last_wake_time >= 0.8:
            self.last_wake_time = now
            self.log(f"[LUMA] wake word detected; listening {self.COMMAND_WINDOW_SECONDS:.0f}s")
            if self.wake_callback is not None:
                try:
                    self.wake_callback()
                except Exception as e:
                    self.log(f"[LUMA] wake callback failed: {e}")

    def _execute_if_valid(self, text, confidence, had_wake):
        now = time.monotonic()

        if had_wake:
            self._open_command_window()

        # If the utterance was only "Luma", just wait for the next utterance.
        if not text:
            return

        cmd = self._canonical_command(text)

        # Bare commands are ONLY valid while the wake window is open.
        if not had_wake and now > self.command_window_until:
            self.log(f"[LUMA] ignored '{text}' (wake window closed)")
            return

        if cmd is None:
            self.log(f"[LUMA] heard '{text}' but no valid command")
            return

        if confidence < self.min_confidence:
            self.log(f"[LUMA] rejected '{text}' confidence={confidence:.0%}")
            return

        if now - self.last_command_time < 0.6:
            return

        self.last_command_time = now
        self.command_window_until = 0.0
        self.log(f"[LUMA] command '{cmd}' confidence={confidence:.0%}")
        self.callback(cmd, confidence)

    def _worker(self):
        if VOSK_IMPORT_ERROR is not None:
            self.last_error = f"vosk unavailable: {VOSK_IMPORT_ERROR}"
            self.log(f"[LUMA] {self.last_error}")
            return
        if np is None:
            self.last_error = "numpy unavailable"
            self.log("[LUMA] numpy unavailable")
            return
        if not self.model_path.is_dir():
            self.last_error = f"model missing: {self.model_path}"
            self.log(f"[LUMA] {self.last_error}")
            return

        try:
            if SetLogLevel is not None:
                SetLogLevel(-1)

            model = Model(str(self.model_path))

            # Include wake-only, wake+command and bare command phrases.
            # Bare commands are recognized acoustically but are ignored unless
            # a recent wake word has opened the command window.
            phrases = set(self.COMMANDS) | set(self.ALIASES.keys())
            grammar = []
            grammar.extend(self.WAKE_ALIASES)
            grammar.extend(phrases)
            for wake in self.WAKE_ALIASES:
                grammar.extend(f"{wake} {p}" for p in phrases)
            grammar.append("[unk]")

            rec = KaldiRecognizer(model, self.SAMPLE_RATE, json.dumps(sorted(set(grammar))))
            rec.SetWords(True)

            self.ready = True
            self.log(
                f"[LUMA] ready, two-stage wake mode, "
                f"window={self.COMMAND_WINDOW_SECONDS:.0f}s, commands={len(self.COMMANDS)}"
            )

            while not self._stop.is_set():
                try:
                    pcm = self._q.get(timeout=0.25)
                except queue.Empty:
                    continue

                if pcm is None:
                    break

                if not rec.AcceptWaveform(pcm):
                    continue

                result = json.loads(rec.Result() or "{}")
                text = self._norm(result.get("text", ""))
                if not text:
                    continue

                conf = self._confidence(result)
                self.last_text = text
                self.last_confidence = conf

                had_wake, remainder = self._strip_wake(text)
                self._execute_if_valid(remainder, conf, had_wake)

        except Exception as e:
            self.last_error = str(e)
            self.log(f"[LUMA] recognizer failed: {e}")
        finally:
            self.ready = False
