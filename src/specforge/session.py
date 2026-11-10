"""Per-session delivery ledger: never pay twice for the same code.

Claude Code starts one MCP server process per session, so process memory is the session. Every
symbol body sent to the model is recorded with its content hash; asking for it again returns a
one-line reference instead of the body (unless it changed, or force=True). When Claude Code
compacts the conversation, the PreCompact hook touches a marker file and the ledger resets, so
the model is never told "you already have it" about code that was summarised away.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import state_dir

MARKER = "compact-marker"
SNAPSHOT = "session-snapshot.json"


class Session:
    def __init__(self, root: Path):
        self.root = root
        self.started = time.time()
        self.delivered: dict[int, str] = {}
        self.resent_avoided = 0
        self.tokens_avoided = 0
        self.queries: list[dict] = []
        self.labels: dict[int, str] = {}
        self._last_save = 0.0

    def _check_compaction(self) -> None:
        m = state_dir(self.root) / MARKER
        try:
            if m.stat().st_mtime > self.started:
                self.reset()
        except OSError:
            pass

    def reset(self) -> None:
        """Called when compaction is detected: keep the old session as the 'previous' snapshot."""
        self.save(previous=True)
        self.delivered.clear()
        self.queries.clear()
        self.labels.clear()
        self.started = time.time()

    def note(self, tool: str, arg: str) -> None:
        self.queries.append({"t": round(time.time() - self.started), "tool": tool, "arg": str(arg)[:120]})
        self.queries = self.queries[-200:]
        if time.time() - self._last_save > 10:
            self.save()

    def snapshot(self) -> dict:
        return {"started": time.strftime("%H:%M:%S", time.localtime(self.started)),
                "symbols_in_context": [self.labels.get(i, str(i)) for i in self.delivered][-150:],
                "recent_queries": self.queries[-40:], "resends_avoided": self.resent_avoided,
                "tokens_not_resent": self.tokens_avoided}

    def save(self, previous: bool = False) -> None:
        self._last_save = time.time()
        p = state_dir(self.root) / SNAPSHOT
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        if previous:
            data["previous"] = self.snapshot()
        else:
            data["current"] = self.snapshot()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError:
            pass

    def load_previous(self) -> dict | None:
        try:
            return json.loads((state_dir(self.root) / SNAPSHOT).read_text(encoding="utf-8")).get("previous")
        except (OSError, json.JSONDecodeError):
            return None

    def ledger(self) -> dict[int, str]:
        self._check_compaction()
        return self.delivered

    def seen(self, sid: int, body_hash: str) -> bool:
        return self.ledger().get(sid) == body_hash

    def mark(self, sid: int, body_hash: str) -> None:
        self.ledger()[sid] = body_hash

    def avoided(self, tokens: int) -> None:
        self.resent_avoided += 1
        self.tokens_avoided += tokens


def touch_compaction_marker(root: Path) -> None:
    d = state_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / MARKER).write_text(str(time.time()), encoding="utf-8")
