#!/usr/bin/env python3
"""Write docs/openapi.json from the FastAPI app. Needs no running database.

    python scripts/export-openapi.py            # (re)generate
    python scripts/export-openapi.py --check    # exit 1 if the committed file is stale
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from app.main import app  # noqa: E402  (importing builds the engine lazily; it never connects)

OUT = ROOT / "docs" / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    fresh = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != fresh:
            sys.exit(f"{OUT.relative_to(ROOT)} is stale; run: python scripts/export-openapi.py")
        print("openapi.json is up to date")
    else:
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(fresh)
        print(f"wrote {OUT.relative_to(ROOT)} ({len(fresh)} bytes)")
