"""Guards against the docs drifting from the code."""

import json
import re
from pathlib import Path

from app.main import app

DOCS = Path(__file__).resolve().parents[2] / "docs"
METHODS = {"get", "post", "put", "patch", "delete"}


def operations() -> set[str]:
    return {
        f"{method.upper()} {path}"
        for path, ops in app.openapi()["paths"].items()
        for method in ops
        if method in METHODS
    }


def test_every_endpoint_is_documented_in_api_md():
    text = (DOCS / "api.md").read_text()
    missing = sorted(op for op in operations() if f"`{op}`" not in text)
    assert not missing, f"endpoints missing from docs/api.md: {missing}"


def test_api_md_does_not_document_removed_endpoints():
    text = (DOCS / "api.md").read_text()
    documented = set(re.findall(r"^### `((?:GET|POST|PUT|PATCH|DELETE) [^`]+)`", text, re.M))
    # Runner endpoints are listed in a table in the appendix, not as headings.
    stale = sorted(documented - operations())
    assert not stale, f"docs/api.md documents endpoints that no longer exist: {stale}"


def test_committed_openapi_json_is_current():
    committed = json.loads((DOCS / "openapi.json").read_text())
    assert committed == app.openapi(), "docs/openapi.json is stale; run: python scripts/export-openapi.py"


def test_relative_links_in_docs_resolve():
    broken = []
    for md in [*DOCS.glob("*.md"), DOCS.parent / "README.md"]:
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", md.read_text()):
            if re.match(r"[a-z]+:", target):
                continue  # http(s):, mailto:
            if not (md.parent / target).resolve().exists():
                broken.append(f"{md.name} -> {target}")
    assert not broken, f"broken relative links: {broken}"
