"""Standard-stream configuration.

The platform handles non-ASCII data — names, labels and categories arrive in
whatever language the source uses — and the
preserved research code prints progress with characters like U+2713. On a
Windows console defaulting to cp950 that raises UnicodeEncodeError mid-request,
so the streams are pinned to UTF-8 once at start-up.
"""

from __future__ import annotations

import sys


def configure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                #  Streams that are not real text files (captured in tests,
                #  piped by a supervisor) are already safe to write to.
                pass
