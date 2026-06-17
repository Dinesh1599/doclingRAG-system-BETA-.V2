"""Google Drive sync via the `rclone` CLI (service-account auth).

Mirrors a Drive *input* folder to a local staging dir, then moves each file to a
Drive *processed* or *skipped* folder after the pipeline decides. Drive paths are
rclone "remote:path" strings, configured via env (defaults assume an rclone remote
named `gdrive` and a `DoclinRAG/{input,processed,skipped}` folder layout):

    GDRIVE_INPUT      gdrive:DoclinRAG/input
    GDRIVE_PROCESSED  gdrive:DoclinRAG/processed
    GDRIVE_SKIPPED    gdrive:DoclinRAG/skipped

Setup (one-time, your Google account):
  1. Create a GCP service account, enable the Drive API, download its JSON key.
  2. Create the 3 Drive folders and share them with the service account email.
  3. Configure an rclone remote:  rclone config  (type=drive,
     service_account_file=/path/key.json)  named to match GDRIVE_REMOTE.
"""

import os
import shutil
import subprocess
from pathlib import Path


def _p(env: str, default: str) -> str:
    return os.environ.get(env, default)


def input_path() -> str:
    return _p("GDRIVE_INPUT", "gdrive:DoclinRAG/input")


def processed_path() -> str:
    return _p("GDRIVE_PROCESSED", "gdrive:DoclinRAG/processed")


def skipped_path() -> str:
    return _p("GDRIVE_SKIPPED", "gdrive:DoclinRAG/skipped")


class RcloneError(RuntimeError):
    pass


def _rclone(*args: str) -> subprocess.CompletedProcess:
    if not shutil.which("rclone"):
        raise RcloneError("rclone not installed (brew install rclone)")
    try:
        return subprocess.run(["rclone", *args], check=True,
                              capture_output=True, text=True)
    except subprocess.CalledProcessError as e:  # surface rclone's stderr
        raise RcloneError(f"rclone {' '.join(args)} failed: {e.stderr.strip()}") from e


def list_input() -> list[str]:
    """PDF filenames currently in the Drive input folder."""
    out = _rclone("lsf", "--include", "*.pdf", input_path())
    return [ln.strip().rstrip("/") for ln in out.stdout.splitlines() if ln.strip()]


def pull(dest: Path) -> list[Path]:
    """Copy the Drive input PDFs into a local staging dir. Returns local paths."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    _rclone("copy", "--include", "*.pdf", input_path(), str(dest))
    return sorted(dest.glob("*.pdf"))


def archive(name: str, relevant: bool) -> None:
    """Move a file out of Drive input -> Drive processed (relevant) or skipped."""
    target = processed_path() if relevant else skipped_path()
    _rclone("moveto", f"{input_path()}/{name}", f"{target}/{name}")
