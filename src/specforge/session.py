"""Per-session delivery ledger: never pay twice for the same code.

Claude Code starts one MCP server process per session, so process memory is the session. Every
symbol body sent to the model is recorded with its content hash; asking for it again returns a
one-line reference instead of the body (unless it changed, or force=True). When Claude Code
compacts the conversation, the PreCompact hook touches a marker file and the ledger resets, so
the model is never told "you already have it" about code that was summarised away.
"""

from __future__ import annotations

import time
from pathlib import Path

from .config import state_dir

MARKER = "compact-marker"


class Session:
    def __init__(self, root: Path):
        self.root = root
        self.started = time.time()
        self.delivered: dict[int, str] = {}
        self.resent_avoided = 0
        self.tokens_avoided = 0

    def _check_compaction(self) -> None:
        m = state_dir(self.root) / MARKER
        try:
            if m.stat().st_mtime > self.started:
                self.reset()
        except OSError:
            pass

    def reset(self) -> None:
        self.delivered.clear()
        self.started = time.time()

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
