"""Docling Serve client returning the structured DoclingDocument JSON.

We request `to_formats=json` (NOT markdown): markdown flattens tables and drops
cell coordinates / page positions that the deterministic extractors and per-row
Source Page provenance depend on.

The parse is cached next to the input as `<pdfname>.docling.json` and reused if
present and newer than the PDF, so a 746-page OCR run happens once.
"""

import json
import sys
import time
from pathlib import Path

import httpx

_TERMINAL_OK = {"success", "succeeded", "completed", "done"}
_TERMINAL_FAIL = {"failure", "failed", "error", "revoked"}


class DoclingError(RuntimeError):
    pass


class DoclingClient:
    def __init__(self, base_url: str, timeout: float = 7200.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=10.0) as c:
                return c.get(f"{self.base_url}/health").status_code == 200
        except httpx.HTTPError:
            return False

    def convert_file(self, file_path: Path, poll_interval: float = 3.0) -> dict:
        """Submit a file to the async API, poll to completion, return the
        DoclingDocument JSON dict. The async path avoids the synchronous
        endpoint's gateway timeout on large / OCR-heavy PDFs."""
        file_path = Path(file_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(file_path)

        data = {
            "to_formats": "json",
            "do_ocr": "true",
            "do_table_structure": "true",
            "table_mode": "accurate",
            "image_export_mode": "placeholder",
        }
        deadline = time.monotonic() + self.timeout
        with httpx.Client(timeout=120.0) as client:
            with open(file_path, "rb") as fh:
                files = {"files": (file_path.name, fh, "application/pdf")}
                resp = client.post(f"{self.base_url}/v1/convert/file/async",
                                   files=files, data=data)
                resp.raise_for_status()
            task = resp.json()
            task_id = task.get("task_id")
            if not task_id:
                raise DoclingError(f"No task_id in async response: {task!r}")

            while True:
                status = self._status(client, task_id)
                if status in _TERMINAL_OK:
                    break
                if status in _TERMINAL_FAIL:
                    raise DoclingError(f"Docling task {task_id} {status}")
                if time.monotonic() > deadline:
                    raise DoclingError(f"Docling task {task_id} timed out (>{self.timeout}s)")
                time.sleep(poll_interval)

            result = client.get(f"{self.base_url}/v1/result/{task_id}", timeout=300.0)
            result.raise_for_status()
            payload = result.json()

        doc = self._extract_document(payload)
        if doc is None:
            raise DoclingError(f"No DoclingDocument JSON in result keys={list(payload)}")
        return doc

    def _status(self, client: httpx.Client, task_id: str) -> str:
        r = client.get(f"{self.base_url}/v1/status/poll/{task_id}", timeout=60.0)
        r.raise_for_status()
        return str(r.json().get("task_status", "")).lower()

    @staticmethod
    def _extract_document(payload: dict) -> dict | None:
        document = payload.get("document", payload)
        if not isinstance(document, dict):
            return None
        # docling-serve nests the structured doc under json_content (or *_content).
        for key in ("json_content", "json", "docling_document"):
            val = document.get(key)
            if isinstance(val, dict):
                return val
        # Some builds return the DoclingDocument fields at the top level.
        if "texts" in document or "tables" in document or "pages" in document:
            return document
        return None

    def convert_cached(self, file_path: Path, force: bool = False) -> dict:
        """Convert with on-disk cache `<pdf>.docling.json` (reuse if newer)."""
        file_path = Path(file_path).resolve()
        cache = file_path.with_suffix(file_path.suffix + ".docling.json")
        if not force and cache.exists() and cache.stat().st_mtime >= file_path.stat().st_mtime:
            return json.loads(cache.read_text())
        doc = self.convert_file(file_path)
        cache.write_text(json.dumps(doc))
        return doc


def _smoke(argv: list[str]) -> int:
    from .config import Config

    if len(argv) < 2:
        print("usage: python -m rate_filing.docling_client <file.pdf>", file=sys.stderr)
        return 2
    cfg = Config.from_env()
    client = DoclingClient(cfg.docling_serve_url)
    if not client.health():
        print(f"docling-serve not reachable at {cfg.docling_serve_url}", file=sys.stderr)
        return 1
    doc = client.convert_cached(Path(argv[1]))
    print("texts:", len(doc.get("texts", [])), "tables:", len(doc.get("tables", [])),
          "pages:", len(doc.get("pages", {}) or doc.get("pages", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(_smoke(sys.argv))
