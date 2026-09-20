#!/usr/bin/env python3
"""Offline SongDB compact-index sanity test using stored fingerprints."""
import sys
from pathlib import Path
ROOT = Path("/home/jenna/collarpet")
sys.path.insert(0, str(ROOT))
import numpy as np
import collarpet as cp

db = cp.SongFingerprintDB()
db.load()
fps = sorted(cp.SONG_FP_DIR.glob("*.npz"))
if not fps:
    raise SystemExit("[FAIL] no fingerprints found")

tested = 0
for fp in fps[:20]:
    d = np.load(fp)
    h = np.asarray(d["hashes"], dtype=np.uint32)
    t = np.asarray(d["times"], dtype=np.int32)
    if h.size < 100:
        continue

    # Pick roughly a six-second-ish time slice from the stored fingerprint.
    t0 = int(t[len(t)//3])
    mask = (t >= t0) & (t <= t0 + 130)
    hh = h[mask]
    tt = t[mask] - t0
    if hh.size < 30:
        continue

    m = db.match(hh.tolist(), tt.tolist())
    tested += 1
    print(f"[TEST] {fp.stem} hashes={hh.size} -> {m['display'] if m else 'NO MATCH'} "
          f"votes={m['votes'] if m else 0}")
    if m and m["uuid"] == fp.stem:
        print("[OK] compact index and matcher agree on stored fingerprint")
        raise SystemExit(0)

print(f"[FAIL] no successful self-match in {tested} usable fingerprints")
raise SystemExit(1)
