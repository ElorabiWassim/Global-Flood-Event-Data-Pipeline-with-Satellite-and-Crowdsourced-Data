"""
Helpers shared by every ingestion module.

Goals:
- Read configuration from the environment (never hard-code secrets).
- Download files into ``data/raw/<source>/`` with a metadata sidecar.
- Compute a SHA-256 checksum so re-runs are reproducible.
- Generate a per-run batch_id so ingested rows can be traced back.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from ..config import HTTP_TIMEOUT, RAW_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Batch + checksum
# ---------------------------------------------------------------------------
def new_batch_id(prefix: str = "batch") -> str:
    """Return a unique batch_id for one ingestion run."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{ts}-{uuid.uuid4().hex[:8]}"


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file (streaming)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------
@dataclass
class DownloadResult:
    """Outcome of an attempt to fetch a remote file."""

    source: str
    source_url: str
    file_path: Path
    checksum: str
    bytes_downloaded: int
    downloaded_at: str
    used_fallback: bool = False
    fallback_reason: Optional[str] = None


def download_to(
    *,
    source: str,
    url: str,
    target: Path,
    fallback: Optional[Path] = None,
    headers: Optional[dict] = None,
) -> DownloadResult:
    """Stream-download ``url`` to ``target``. Fall back to ``fallback`` on error.

    The fallback file (if any) is copied unchanged to ``target`` so the rest of
    the pipeline never has to know whether the data is fresh or seeded.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    used_fallback = False
    fallback_reason: Optional[str] = None

    try:
        logger.info("[%s] downloading %s", source, url)
        with requests.get(url, stream=True, timeout=HTTP_TIMEOUT, headers=headers) as r:
            r.raise_for_status()
            with target.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        fh.write(chunk)
    except (requests.RequestException, OSError) as exc:
        if fallback and fallback.exists():
            logger.warning(
                "[%s] download failed (%s) — falling back to %s",
                source,
                exc,
                fallback,
            )
            target.write_bytes(fallback.read_bytes())
            used_fallback = True
            fallback_reason = str(exc)
        else:
            raise

    checksum = sha256_file(target)
    result = DownloadResult(
        source=source,
        source_url=url,
        file_path=target,
        checksum=checksum,
        bytes_downloaded=target.stat().st_size,
        downloaded_at=datetime.now(timezone.utc).isoformat(),
        used_fallback=used_fallback,
        fallback_reason=fallback_reason,
    )
    write_metadata(result)
    return result


def write_metadata(result: DownloadResult) -> None:
    """Write a JSON sidecar next to the downloaded file."""
    sidecar = result.file_path.with_suffix(result.file_path.suffix + ".meta.json")
    payload = asdict(result)
    payload["file_path"] = str(result.file_path)
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("[%s] metadata written -> %s", result.source, sidecar.name)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def raw_subdir(source_slug: str) -> Path:
    """Return (and create) ``data/raw/<source>/``."""
    p = RAW_DIR / source_slug
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Parse-with-fallback
# ---------------------------------------------------------------------------
def parse_with_fallback(reader, primary: Path, fallback: Optional[Path], *, source: str):
    """Run ``reader(primary)``; on any exception, retry with ``fallback``.

    A successful HTTP download does not guarantee a parsable file (servers
    often return HTML error pages with a 200 status). We therefore allow each
    ingestion module to declare a parser and let this helper transparently
    swap to the seed file when parsing fails.
    """
    try:
        return reader(primary), False, None
    except Exception as exc:  # noqa: BLE001
        if fallback and fallback.exists() and fallback != primary:
            logger.warning(
                "[%s] parsing %s failed (%s) — falling back to %s",
                source,
                primary.name,
                type(exc).__name__,
                fallback.name,
            )
            return reader(fallback), True, f"{type(exc).__name__}: {exc}"
        raise
