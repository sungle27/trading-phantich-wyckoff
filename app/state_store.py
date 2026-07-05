from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Optional, Dict


class StateStore:
    """
    Crash-safe state store:
    - Atomic write via temp + os.replace
    - fsync to reduce data loss on sudden power loss
    - Keep .bak fallback
    - Basic validation + safe load
    """

    def __init__(
        self,
        path: str = "runtime_state.json",
        *,
        keep_backup: bool = True,
        backup_suffix: str = ".bak",
        tmp_suffix: str = ".tmp",
        max_bytes: int = 10_000_000,  # guard against accidental huge dumps
    ):
        self.path = path
        self.keep_backup = keep_backup
        self.backup_path = path + backup_suffix
        self.tmp_path = path + tmp_suffix
        self.max_bytes = int(max_bytes)

    # -------------------------
    # Public API
    # -------------------------
    def save(self, state: dict) -> None:
        """
        Save snapshot safely.
        """
        state = self._normalize(state)
        payload = self._encode(state)
        if len(payload) > self.max_bytes:
            raise RuntimeError(f"[StateStore] state too large: {len(payload)} bytes")

        # backup current
        if self.keep_backup and os.path.exists(self.path):
            try:
                # copy bytes (not rename) so current remains if copy fails
                with open(self.path, "rb") as src, open(self.backup_path, "wb") as dst:
                    dst.write(src.read())
                    dst.flush()
                    os.fsync(dst.fileno())
            except Exception:
                # backup failure shouldn't block trading
                pass

        # atomic write new
        self._atomic_write_bytes(self.path, payload)

    def load(self) -> Optional[dict]:
        """
        Load snapshot:
        - try main file
        - if corrupt/missing -> try backup
        """
        obj = self._safe_load_json(self.path)
        if obj is not None and self._validate(obj):
            return obj

        obj_bak = self._safe_load_json(self.backup_path)
        if obj_bak is not None and self._validate(obj_bak):
            return obj_bak

        return None

    # Optional: journal (append-only)
    def append_event(self, event: dict, journal_path: str = "runtime_journal.jsonl") -> None:
        """
        Append-only event log. Useful if later you want replay.
        Crash-safe enough via append+fsync.
        """
        event = self._normalize(event)
        event.setdefault("ts", time.time())
        line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")

        with open(journal_path, "ab") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    # -------------------------
    # Internals
    # -------------------------
    def _atomic_write_bytes(self, final_path: str, data: bytes) -> None:
        # write temp
        with open(self.tmp_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        # atomic replace
        os.replace(self.tmp_path, final_path)

        # fsync directory to persist rename on some filesystems
        try:
            dir_path = os.path.dirname(os.path.abspath(final_path)) or "."
            dir_fd = os.open(dir_path, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            pass

    def _safe_load_json(self, path: str) -> Optional[dict]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                raw = f.read()
                if not raw:
                    return None
                if len(raw) > self.max_bytes:
                    return None
            obj = json.loads(raw.decode("utf-8"))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    def _encode(self, obj: dict) -> bytes:
        # stable ordering helps diffs/debug
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")

    def _normalize(self, obj: Any) -> Any:
        """
        Convert dataclasses and other non-serializables into JSON-friendly forms.
        """
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, dict):
            return {str(k): self._normalize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._normalize(x) for x in obj]
        # primitives
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        # fallback: string representation (avoid crashing during save)
        return str(obj)

    def _validate(self, st: dict) -> bool:
        """
        Lightweight schema check: adjust as needed.
        """
        # Must have nav and sim positions at least
        if "nav" not in st and "sim" not in st:
            # allow older formats but typically we want nav/sim
            return False

        # if present, sim.positions should be dict
        sim = st.get("sim")
        if sim is not None:
            pos = sim.get("positions")
            if pos is not None and not isinstance(pos, dict):
                return False

        return True