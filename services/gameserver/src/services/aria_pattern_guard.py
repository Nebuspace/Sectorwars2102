"""ADR-0057 A-V1 layer 4 -- versioned pattern-list guard, defense-in-depth
only (layers 3+5 are the load-bearing defenses). Loads
src/aria/security/patterns.json and exposes filter(), which replaces
matched spans with "[filtered]" -- the EXACT same non-blocking behavior
enhanced_ai_service.py's now-removed _filter_prompt_injections had, just
sourced from a versioned, hot-reloadable JSON file instead of a hardcoded
regex list (WO-ARIA-PROMPT-DEFENSE Accept #4).

Hot-reload: every call cheaply stat()s patterns.json's mtime and only
re-parses the JSON body when the mtime (or the file's own "version" field)
changed -- a PR-reviewed patterns.json update takes effect on the next
call, no process restart needed.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import List, NamedTuple, Optional

logger = logging.getLogger(__name__)

PATTERNS_PATH = Path(__file__).resolve().parent.parent / "aria" / "security" / "patterns.json"


class CompiledPattern(NamedTuple):
    regex: re.Pattern
    raw: str
    cls: str
    action: str


class AriaPatternGuard:
    """Loads + hot-reloads the versioned pattern list. get_pattern_guard()
    returns a process-wide singleton; tests construct their own instance
    (optionally pointed at a fixture path) for isolation."""

    def __init__(self, path: Path = PATTERNS_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._loaded_version: Optional[int] = None
        self._loaded_mtime: float = -1.0
        self._patterns: List[CompiledPattern] = []
        self._reload_if_needed(force=True)

    def _reload_if_needed(self, *, force: bool = False) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except OSError as e:
            logger.error("aria_pattern_guard: patterns.json unreadable (%s) -- keeping last-loaded set", e)
            return

        if not force and mtime == self._loaded_mtime:
            return

        with self._lock:
            try:
                data = json.loads(self._path.read_text())
            except (OSError, json.JSONDecodeError) as e:
                logger.error("aria_pattern_guard: failed to parse patterns.json (%s) -- keeping last-loaded set", e)
                return

            version = data.get("version")
            compiled: List[CompiledPattern] = []
            for entry in data.get("patterns", []):
                try:
                    compiled.append(CompiledPattern(
                        regex=re.compile(entry["pattern"], re.IGNORECASE),
                        raw=entry["pattern"],
                        cls=entry.get("class", "unknown"),
                        action=entry.get("action", "filter"),
                    ))
                except re.error as e:
                    logger.error("aria_pattern_guard: skipping invalid pattern %r (%s)", entry.get("pattern"), e)

            self._patterns = compiled
            self._loaded_version = version
            self._loaded_mtime = mtime
            logger.info(
                "aria_pattern_guard: loaded patterns.json version=%s (%d patterns)",
                version, len(compiled),
            )

    @property
    def version(self) -> Optional[int]:
        self._reload_if_needed()
        return self._loaded_version

    @property
    def pattern_count(self) -> int:
        self._reload_if_needed()
        return len(self._patterns)

    def filter(self, text: str) -> str:
        """Layer 4: replace every matched span with '[filtered]' -- the
        same non-blocking transform _filter_prompt_injections used to
        perform. Every shipped entry is action="filter" today (see
        patterns.json's own docstring); the schema is block-capable for a
        future WO without needing another migration."""
        if not text:
            return text
        self._reload_if_needed()
        filtered = text
        for p in self._patterns:
            if p.regex.search(filtered):
                logger.warning("aria_pattern_guard: matched %s pattern (class=%s)", p.raw, p.cls)
                filtered = p.regex.sub("[filtered]", filtered)
        return filtered

    def matches(self, text: str) -> List[str]:
        """Non-mutating: which pattern classes matched, if any."""
        if not text:
            return []
        self._reload_if_needed()
        return [p.cls for p in self._patterns if p.regex.search(text)]


_guard_instance: Optional[AriaPatternGuard] = None
_guard_lock = threading.Lock()


def get_pattern_guard() -> AriaPatternGuard:
    """Process-wide singleton, matching get_security_service()/
    get_ai_provider_service()'s established factory convention."""
    global _guard_instance
    if _guard_instance is None:
        with _guard_lock:
            if _guard_instance is None:
                _guard_instance = AriaPatternGuard()
    return _guard_instance
