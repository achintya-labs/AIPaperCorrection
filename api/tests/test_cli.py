import importlib.util
from pathlib import Path

import pytest

CLI_PATH = Path(__file__).resolve().parents[2] / "scripts" / "pg-cli.py"
spec = importlib.util.spec_from_file_location("pg_cli", CLI_PATH)
assert spec and spec.loader
pg_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg_cli)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"])
def test_cli_allows_local_urls(url):
    assert pg_cli.validate_local_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://192.168.1.10:8000",
        "http://api:8000",
        "ftp://127.0.0.1:8000",
    ],
)
def test_cli_rejects_non_local_urls(url):
    with pytest.raises(ValueError, match="non-local"):
        pg_cli.validate_local_url(url)


def test_cli_parser_covers_core_commands():
    p = pg_cli.build_parser()
    assert p.parse_args(["papers", "pending", "--exam", "1"]).exam == 1
    assert p.parse_args(["grades", "export-pdf", "7", "-o", "out.pdf"]).output == "out.pdf"
    assert p.parse_args(["import", "auto-split", "1", "scan.pdf", "--wait"]).wait is True
