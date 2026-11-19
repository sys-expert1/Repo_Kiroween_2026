"""Export one real REVIVE run (events and artifacts) for the frontend contract test.

Run from backend/: `.venv/bin/python -m tests.export_ui_fixture`. The run uses the same offline
doubles as the test suite, so the output is deterministic in shape. It is produced by the real
workflow, not written by hand, so the frontend test fails if the backend contract drifts.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import FIXTURES_DIR, Settings
from app.graph.runner import InlineExecutor
from app.main import create_app
from tests.conftest import OfflineAdapter, SequencedRunner, canned_output
from tests.helpers import parse_sse

OLD = "tests/test_inventory.py::test_total_items_known_failure"
OUT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "test" / "real-run.json"


def export(outcome: str) -> dict:
    """outcome: 'verified' (patches pass) or 'rolled_back' (a new test fails after patching)."""
    final = canned_output(1, [OLD]) if outcome == "verified" else canned_output(0, [OLD, "t::new"])
    runner = SequencedRunner([canned_output(1, [OLD]), final])
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            data_dir=Path(tmp) / "data", allowed_roots=(FIXTURES_DIR.resolve(),),
            fixtures_dir=FIXTURES_DIR.resolve(), sandbox_mode="off",
        )  # fmt: skip
        app = create_app(
            settings, executor=InlineExecutor(), adapter=OfflineAdapter(),
            runner_factory=lambda st, home: runner if st == "fixture" else None,
        )  # fmt: skip
        app.state.sse_poll_seconds = 0.01
        c = TestClient(app)
        repo = c.post("/api/repositories/import", json={"fixture_id": "legacy-python-cli"}).json()
        run = c.post(
            "/api/revive/start", json={"repository_id": repo["id"], "mode": "REVIVE"}
        ).json()
        waiting = c.get(f"/api/revive/{run['id']}").json()
        c.post(
            f"/api/revive/approve/{run['id']}",
            json={"decision": "approve", "roadmap_version": run["roadmap_version"]},
        )
        done = c.get(f"/api/revive/{run['id']}").json()
        arts = c.get(f"/api/revive/{run['id']}/artifacts").json()
        by_type: dict[str, object] = {}
        for a in arts:
            if a["type"].endswith("_data") or a["type"] == "vitality_report":
                by_type[a["type"]] = json.loads(
                    c.get(f"/api/revive/{run['id']}/artifacts/{a['id']}").text
                )
        return {
            "waiting_run": waiting,
            "final_run": done,
            "artifacts": arts,
            "data": by_type,
            "events": parse_sse(c.get(f"/api/revive/stream/{run['id']}").text),
        }


if __name__ == "__main__":
    out = {"verified": export("verified"), "rolled_back": export("rolled_back")}
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
