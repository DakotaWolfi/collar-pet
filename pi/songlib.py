#!/usr/bin/env python3
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import csv
import uuid as uuidlib
import numpy as np

CATALOG_FIELDS = [
    "uuid", "artist", "title", "album", "year", "duration",
    "source_filename", "musicbrainz_id", "acoustid_id", "enabled", "notes"
]

@dataclass
class SongMeta:
    uuid: str
    artist: str
    title: str
    album: str = ""
    year: str = ""
    duration: str = ""
    source_filename: str = ""
    musicbrainz_id: str = ""
    acoustid_id: str = ""
    enabled: bool = True
    notes: str = ""

    @classmethod
    def from_row(cls, row):
        return cls(
            uuid=row.get("uuid", "").strip(),
            artist=row.get("artist", "").strip(),
            title=row.get("title", "").strip(),
            album=row.get("album", "").strip(),
            year=row.get("year", "").strip(),
            duration=row.get("duration", "").strip(),
            source_filename=row.get("source_filename", "").strip(),
            musicbrainz_id=row.get("musicbrainz_id", "").strip(),
            acoustid_id=row.get("acoustid_id", "").strip(),
            enabled=str(row.get("enabled", "1")).strip().lower() not in ("0","false","no","off"),
            notes=row.get("notes", "").strip(),
        )

    def to_row(self):
        return {
            "uuid": self.uuid,
            "artist": self.artist,
            "title": self.title,
            "album": self.album,
            "year": self.year,
            "duration": self.duration,
            "source_filename": self.source_filename,
            "musicbrainz_id": self.musicbrainz_id,
            "acoustid_id": self.acoustid_id,
            "enabled": "1" if self.enabled else "0",
            "notes": self.notes,
        }

def ensure_catalog(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CATALOG_FIELDS).writeheader()

def load_catalog(path: Path):
    ensure_catalog(path)
    out = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            meta = SongMeta.from_row(row)
            if meta.uuid:
                out[meta.uuid] = meta
    return out

def save_catalog(path: Path, songs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        w.writeheader()
        for meta in sorted(songs.values(), key=lambda x: ((x.artist or "").lower(), (x.title or "").lower(), x.uuid)):
            w.writerow(meta.to_row())

def new_uuid():
    return str(uuidlib.uuid4())

class SongFingerprintDBv3:
    """UUID-based CollarPet fingerprint library.

    fingerprints/<uuid>.npz stores machine-generated hashes.
    songs.csv stores editable metadata.
    """
    def __init__(self, root: Path):
        self.root = Path(root)
        self.catalog_file = self.root / "songs.csv"
        self.fp_dir = self.root / "fingerprints"
        self.fp_dir.mkdir(parents=True, exist_ok=True)
        self.songs = {}
        self.index = {}
        self.reload()

    def reload(self):
        self.songs = load_catalog(self.catalog_file)
        self.index = {}
        loaded = 0
        for uid, meta in self.songs.items():
            if not meta.enabled:
                continue
            fp = self.fp_dir / f"{uid}.npz"
            if not fp.exists():
                continue
            try:
                with np.load(fp, allow_pickle=False) as z:
                    hashes = z["hashes"].astype(np.uint32)
                    times = z["times"].astype(np.int32)
                for h, t in zip(hashes.tolist(), times.tolist()):
                    self.index.setdefault(int(h), []).append((uid, int(t)))
                loaded += 1
            except Exception as e:
                print(f"[SONGDB] failed {fp.name}: {e}")
        print(f"[SONGDB] v3 loaded {loaded} song(s), {len(self.index)} unique hashes")

    def get(self, uid):
        return self.songs.get(uid)

    def match(self, hashes, times, min_hash_votes=14, min_unique_hashes=10):
        # Same offset-voting approach used by CollarPet's existing matcher,
        # but identity is UUID rather than filename/title.
        if not hashes:
            return None

        votes = {}
        unique = {}
        for h, qt in zip(hashes, times):
            for uid, rt in self.index.get(int(h), ()):
                off = int(rt) - int(qt)
                key = (uid, off)
                votes[key] = votes.get(key, 0) + 1
                unique.setdefault(key, set()).add(int(h))

        if not votes:
            return None

        ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
        (best_uid, best_off), best_votes = ranked[0]
        best_unique = len(unique.get((best_uid, best_off), ()))
        if best_votes < min_hash_votes or best_unique < min_unique_hashes:
            return None

        second = 0
        for (uid, _), v in ranked[1:]:
            if uid != best_uid:
                second = v
                break

        meta = self.songs.get(best_uid)
        if meta is None:
            return None

        return {
            "uuid": best_uid,
            "artist": meta.artist,
            "title": meta.title,
            "album": meta.album,
            "year": meta.year,
            "votes": best_votes,
            "unique_hashes": best_unique,
            "second_votes": second,
            "offset": best_off,
            "display": f"{meta.artist} - {meta.title}" if meta.artist else meta.title,
        }
