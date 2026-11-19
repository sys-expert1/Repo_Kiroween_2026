from __future__ import annotations

import json


def parse_sse(body: str) -> list[dict]:
    """Parse an SSE body into events: the SSE `id`/`event` plus the JSON envelope fields."""
    events = []
    for block in body.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "data" in fields:
            events.append(
                {"id": fields["id"], "event": fields["event"], **json.loads(fields["data"])}
            )
    return events
