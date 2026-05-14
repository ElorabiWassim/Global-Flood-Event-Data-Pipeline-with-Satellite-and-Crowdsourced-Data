"""Unit tests for db.client._clean_for_json (the NaN-safe JSON sanitizer)."""

from __future__ import annotations

import json
import math

import pytest

import db.client as db_client
from db.client import _clean_for_json, insert_social_media_posts


class TestCleanForJsonScalars:
    def test_plain_values_pass_through(self):
        assert _clean_for_json(1) == 1
        assert _clean_for_json("hello") == "hello"
        assert _clean_for_json(None) is None
        assert _clean_for_json(True) is True

    def test_finite_float_passes_through(self):
        assert _clean_for_json(1.5) == 1.5
        assert _clean_for_json(0.0) == 0.0
        assert _clean_for_json(-3.14) == -3.14

    def test_nan_becomes_none(self):
        assert _clean_for_json(float("nan")) is None

    def test_positive_inf_becomes_none(self):
        assert _clean_for_json(float("inf")) is None

    def test_negative_inf_becomes_none(self):
        assert _clean_for_json(float("-inf")) is None


class TestCleanForJsonContainers:
    def test_dict_recurses(self):
        out = _clean_for_json({"a": 1, "b": float("nan"), "c": "x"})
        assert out == {"a": 1, "b": None, "c": "x"}

    def test_list_recurses(self):
        out = _clean_for_json([1, float("inf"), "x", None])
        assert out == [1, None, "x", None]

    def test_tuple_becomes_list(self):
        # Postgres JSON has no tuple type; collapsing to list is correct.
        out = _clean_for_json((1, 2, float("nan")))
        assert out == [1, 2, None]

    def test_deeply_nested(self):
        payload = {
            "events": [
                {"id": 1, "lat": float("nan"), "tags": ["a", float("inf")]},
                {"id": 2, "lat": 12.5, "tags": []},
            ],
            "meta": {"count": 2, "ratio": float("-inf")},
        }
        out = _clean_for_json(payload)
        assert out == {
            "events": [
                {"id": 1, "lat": None, "tags": ["a", None]},
                {"id": 2, "lat": 12.5, "tags": []},
            ],
            "meta": {"count": 2, "ratio": None},
        }


class TestCleanForJsonRoundTrip:
    """The cleaned output must be valid strict JSON (allow_nan=False)."""

    def test_strict_json_serializable_after_clean(self):
        payload = {
            "lat": float("nan"),
            "lon": 12.5,
            "extras": {"score": float("inf")},
            "tags": [1, float("-inf"), "x"],
        }
        cleaned = _clean_for_json(payload)
        # This must NOT raise. json.dumps with allow_nan=False is the same
        # serialiser used by db.client.insert_raw_records().
        json.dumps(cleaned, allow_nan=False)

    def test_naive_dump_would_fail_without_cleaning(self):
        """Sanity-check the regression motivating _clean_for_json's existence."""
        try:
            json.dumps({"x": float("nan")}, allow_nan=False)
        except ValueError:
            pass
        else:  # pragma: no cover — would mean stdlib changed
            raise AssertionError("json.dumps(allow_nan=False) unexpectedly accepted NaN")

    def test_unknown_objects_pass_through(self):
        # We only sanitise floats / containers. Other types are left for the
        # JSON encoder's ``default=str`` to handle in insert_raw_records.
        assert _clean_for_json(b"bytes") == b"bytes"


def test_isnan_helper_consistency():
    # Sanity guard: math.isnan must agree with our heuristic — keeps regressions
    # obvious if someone "optimises" the float check.
    assert math.isnan(float("nan"))
    assert _clean_for_json(float("nan")) is None


class TestInsertSocialMediaPosts:
    def test_empty_records_do_not_execute(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            db_client,
            "execute_with_retry",
            lambda sql, params: calls.append((sql, params)),
        )

        out = insert_social_media_posts(
            [],
            platform="bluesky",
            source="Bluesky",
            source_url="https://example.test",
            file_path=None,
            batch_id="batch-1",
        )

        assert out == 0
        assert calls == []

    def test_builds_social_media_rows_and_upsert_sql(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            db_client,
            "execute_with_retry",
            lambda sql, params: calls.append((str(sql), params)),
        )

        out = insert_social_media_posts(
            [
                {
                    "post_id": "post-1",
                    "text": "Flooding near the river",
                    "score": float("nan"),
                }
            ],
            platform="bluesky",
            source="Bluesky",
            source_url="https://bsky.app",
            file_path="data/raw/social_media/bluesky/snapshot.json",
            batch_id="bluesky-1",
        )

        assert out == 1
        assert len(calls) == 1
        sql, params = calls[0]
        assert "INSERT INTO raw.social_media_posts" in sql
        assert "ON CONFLICT (platform, post_id) DO UPDATE" in sql
        assert params[0]["platform"] == "bluesky"
        assert params[0]["source"] == "Bluesky"
        assert params[0]["post_id"] == "post-1"
        assert params[0]["batch_id"] == "bluesky-1"
        assert json.loads(params[0]["payload"]) == {
            "post_id": "post-1",
            "text": "Flooding near the river",
            "score": None,
        }

    def test_missing_post_id_raises_clear_error(self):
        with pytest.raises(ValueError, match="post_id"):
            insert_social_media_posts(
                [{"text": "Flooding near the river"}],
                platform="bluesky",
                source="Bluesky",
                source_url="https://bsky.app",
                file_path=None,
                batch_id="bluesky-1",
            )
