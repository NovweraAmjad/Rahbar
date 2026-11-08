from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from corpus import fetch


def test_normalise_encodes_spaces_and_converts_google_drive_links() -> None:
    assert fetch.normalise(
        "https://nu.edu.pk/Admissions/ProspectusUpload/Academic Rules.pdf"
    ) == "https://nu.edu.pk/Admissions/ProspectusUpload/Academic%20Rules.pdf"
    assert fetch.normalise(
        "https://drive.google.com/file/d/abc123/view?usp=sharing"
    ) == "https://drive.google.com/uc?export=download&id=abc123"


def test_fetch_downloads_duplicate_url_once_and_records_original_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    manifest = tmp_path / "MANIFEST.md"
    source_url = "https://example.test/Policy Document.pdf"
    monkeypatch.setattr(fetch, "RAW", raw)
    monkeypatch.setattr(fetch, "MANIFEST", manifest)
    monkeypatch.setattr(
        fetch,
        "load_sources",
        lambda: [
            {"name": "Policy", "url": source_url, "doc_version": "2026"},
            {"name": "Policy copy", "url": source_url, "doc_version": "2026"},
        ],
    )
    requests: list[str] = []
    sleeps: list[float] = []

    class Client:
        def get(self, url: str) -> httpx.Response:
            requests.append(url)
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.7 test",
                request=httpx.Request("GET", url),
            )

    class ClientContext:
        def __enter__(self) -> Client:
            return Client()

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(fetch.httpx, "Client", lambda **_: ClientContext())
    monkeypatch.setattr(fetch.time, "sleep", sleeps.append)

    assert fetch.fetch(force=False) == 0
    assert requests == ["https://example.test/Policy%20Document.pdf"]
    assert sleeps == [fetch.PAUSE_SECONDS]
    entry = json.loads((raw / "_index.json").read_text(encoding="utf-8"))[0]
    assert entry["url"] == source_url
    assert entry["sha256"] == hashlib.sha256(b"%PDF-1.7 test").hexdigest()
    assert f"<{source_url}>" in manifest.read_text(encoding="utf-8")


def test_fetch_rejects_non_pdf_payload_for_pdf_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    old_index = raw / "_index.json"
    old_index.write_text('[{"name":"old"}]', encoding="utf-8")
    manifest = tmp_path / "MANIFEST.md"
    manifest.write_text("# existing manifest\n", encoding="utf-8")
    monkeypatch.setattr(fetch, "RAW", raw)
    monkeypatch.setattr(fetch, "MANIFEST", manifest)
    monkeypatch.setattr(
        fetch,
        "load_sources",
        lambda: [{"name": "Not a PDF", "url": "https://example.test/bad.pdf"}],
    )

    class Client:
        def get(self, url: str) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html>not a PDF</html>",
                request=httpx.Request("GET", url),
            )

    class ClientContext:
        def __enter__(self) -> Client:
            return Client()

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(fetch.httpx, "Client", lambda **_: ClientContext())
    monkeypatch.setattr(fetch.time, "sleep", lambda _: None)

    assert fetch.fetch(force=False) == 1
    assert old_index.read_text(encoding="utf-8") == '[{"name":"old"}]'
    assert manifest.read_text(encoding="utf-8") == "# existing manifest\n"
    assert not list(raw.glob("*.pdf"))


def test_google_drive_download_uses_direct_download_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fetch, "RAW", tmp_path / "raw")
    monkeypatch.setattr(fetch, "MANIFEST", tmp_path / "MANIFEST.md")
    source_url = "https://drive.google.com/file/d/abc123/view?usp=sharing"
    monkeypatch.setattr(
        fetch,
        "load_sources",
        lambda: [{"name": "Drive PDF", "url": source_url}],
    )
    requests: list[str] = []

    class Client:
        def get(self, url: str) -> httpx.Response:
            requests.append(url)
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.7 test",
                request=httpx.Request("GET", url),
            )

    class ClientContext:
        def __enter__(self) -> Client:
            return Client()

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(fetch.httpx, "Client", lambda **_: ClientContext())
    monkeypatch.setattr(fetch.time, "sleep", lambda _: None)

    assert fetch.fetch(force=False) == 0
    assert requests == ["https://drive.google.com/uc?export=download&id=abc123"]
