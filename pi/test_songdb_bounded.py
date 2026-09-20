#!/usr/bin/env python3
import resource
import numpy as np
import collarpet as cp

def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

print(f"[RAM] start {rss_mb():.1f} MiB")
db = cp.SongFingerprintDB()
db.load()
print(f"[RAM] DB mmap loaded {rss_mb():.1f} MiB")

fps = sorted(cp.SONG_FP_DIR.glob("*.npz"))
if not fps:
    raise SystemExit("[FAIL] no fingerprints found")

for fp in fps[:30]:
    with np.load(fp) as d:
        h = np.asarray(d["hashes"], dtype=np.uint32)
        t = np.asarray(d["times"], dtype=np.int32)
    if h.size < 100:
        continue
    t0 = int(t[len(t)//3])
    mask = (t >= t0) & (t <= t0 + 130)
    hh, tt = h[mask], t[mask] - t0
    if hh.size < 30:
        continue
    before = rss_mb()
    m = db.match(hh, tt)
    after = rss_mb()
    if m:
        print(f"[TEST] {fp.stem} q={hh.size} used={m.get('used_landmarks')} skip={m.get('skipped_common')} -> {m['display']} votes={m['votes']} unique={m['unique']} RAM={after:.1f}MiB (+{after-before:.1f})")
    else:
        print(f"[TEST] {fp.stem} q={hh.size} -> NO MATCH RAM={after:.1f}MiB (+{after-before:.1f})")
    if m and m["uuid"] == fp.stem:
        print("[OK] bounded matcher self-match succeeded")
        raise SystemExit(0)

raise SystemExit("[FAIL] no successful self-match in first 30 usable fingerprints")
