"""Raw layer: every response is saved untouched, with a SHA-256 manifest.

A new file is written only when the content differs from the newest version,
so re-pulling unchanged days does not grow the store.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest = root / "manifest.jsonl"

    def latest(self, endpoint: str, key: str, suffix: str = ".json") -> Path | None:
        files = sorted((self.root / endpoint).glob(f"{key}_*{suffix}"))
        return files[-1] if files else None

    def keys(self, endpoint: str, suffix: str = ".json") -> list[str]:
        files = (self.root / endpoint).glob(f"*{suffix}")
        return sorted({f.name.rsplit("_", 1)[0] for f in files})

    def save(self, endpoint: str, key: str, content: bytes, suffix: str = ".json") -> bool:
        """Store `content`; return True if it is new or changed."""
        previous = self.latest(endpoint, key, suffix)
        if previous is not None and previous.read_bytes() == content:
            return False
        now = datetime.now(UTC)
        path = self.root / endpoint / f"{key}_{now:%Y%m%dT%H%M%S%fZ}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        entry = {
            "path": str(path.relative_to(self.root)),
            "sha256": hashlib.sha256(content).hexdigest(),
            "fetched_utc": now.isoformat(timespec="seconds"),
        }
        with self.manifest.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        return True

    def save_json(self, endpoint: str, key: str, payload: object) -> bool:
        return self.save(endpoint, key, json.dumps(payload, sort_keys=True).encode())

    def load_json(self, endpoint: str, key: str) -> object | None:
        path = self.latest(endpoint, key)
        return None if path is None else json.loads(path.read_text())
