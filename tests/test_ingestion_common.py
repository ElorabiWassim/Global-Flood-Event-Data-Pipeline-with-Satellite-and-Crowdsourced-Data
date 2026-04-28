"""Unit tests for ingestion.common helpers (no network, no DB)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ingestion.common import (
    new_batch_id,
    parse_with_fallback,
    sha256_file,
)


class TestNewBatchId:
    def test_returns_string(self):
        bid = new_batch_id()
        assert isinstance(bid, str)
        assert bid.startswith("batch-")

    def test_custom_prefix(self):
        bid = new_batch_id(prefix="dartmouth")
        assert bid.startswith("dartmouth-")

    def test_two_calls_differ(self):
        # Random hex suffix means collisions are astronomically unlikely.
        assert new_batch_id() != new_batch_id()


class TestSha256File:
    def test_matches_manual_hash(self, tmp_path: Path):
        f = tmp_path / "sample.bin"
        payload = b"hello flood pipeline" * 1024  # ~20 KB
        f.write_bytes(payload)

        expected = hashlib.sha256(payload).hexdigest()
        assert sha256_file(f) == expected

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        # SHA-256 of the empty string is a known constant.
        assert sha256_file(f) == (
            "e3b0c44298fc1c149afbf4c8996fb924"
            "27ae41e4649b934ca495991b7852b855"
        )


class TestParseWithFallback:
    def test_success_path_returns_primary(self, tmp_path: Path):
        primary = tmp_path / "primary.csv"
        primary.write_text("ok")

        def reader(p: Path) -> str:
            return p.read_text()

        result, used_fallback, reason = parse_with_fallback(
            reader, primary, fallback=None, source="unit"
        )
        assert result == "ok"
        assert used_fallback is False
        assert reason is None

    def test_falls_back_when_primary_unparsable(self, tmp_path: Path):
        primary = tmp_path / "primary.bin"
        primary.write_bytes(b"<html>not csv</html>")
        fallback = tmp_path / "seed.csv"
        fallback.write_text("seed-data")

        def reader(p: Path) -> str:
            text = p.read_text()
            if "<html>" in text:
                raise ValueError("expected CSV, got HTML")
            return text

        result, used_fallback, reason = parse_with_fallback(
            reader, primary, fallback=fallback, source="unit"
        )
        assert result == "seed-data"
        assert used_fallback is True
        assert reason is not None
        assert "ValueError" in reason

    def test_raises_when_no_fallback_available(self, tmp_path: Path):
        primary = tmp_path / "primary.bin"
        primary.write_bytes(b"junk")

        def reader(p: Path) -> str:
            raise RuntimeError("parser blew up")

        with pytest.raises(RuntimeError):
            parse_with_fallback(reader, primary, fallback=None, source="unit")

    def test_raises_when_fallback_is_missing_file(self, tmp_path: Path):
        primary = tmp_path / "primary.bin"
        primary.write_bytes(b"junk")
        missing_fallback = tmp_path / "does_not_exist.csv"

        def reader(p: Path) -> str:
            raise RuntimeError("parser blew up")

        with pytest.raises(RuntimeError):
            parse_with_fallback(
                reader, primary, fallback=missing_fallback, source="unit"
            )

    def test_does_not_fall_back_to_self(self, tmp_path: Path):
        # The primary IS the fallback (rare but real when the seed file is the
        # only available source). We must not loop forever — the helper must
        # re-raise the original exception.
        primary = tmp_path / "same.csv"
        primary.write_text("junk")

        def reader(p: Path) -> str:
            raise RuntimeError("always fails")

        with pytest.raises(RuntimeError):
            parse_with_fallback(reader, primary, fallback=primary, source="unit")
