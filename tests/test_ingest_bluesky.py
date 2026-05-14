"""Unit tests for the Bluesky ingester (no network, no real DB)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

import ingestion.ingest_bluesky as bluesky


class FakeResponse:
    def __init__(self, payload=None, status_code: int = 200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


def _post(uri: str, text: str, *, handle: str = "user.bsky.social"):
    return {
        "uri": uri,
        "cid": "cid",
        "author": {"did": "did:plc:abc", "handle": handle},
        "record": {
            "text": text,
            "createdAt": "2026-05-13T10:00:00Z",
            "langs": ["en"],
        },
        "indexedAt": "2026-05-13T10:01:00Z",
    }


def test_fetch_search_results_uses_public_endpoint_and_bounds_limit(monkeypatch):
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        return FakeResponse({"posts": [_post("at://did/app.bsky.feed.post/1", "flood")]})

    monkeypatch.setattr(bluesky, "_auth_headers", lambda: None)
    monkeypatch.setattr(bluesky.requests, "get", fake_get)

    out = bluesky._fetch_search_results("flood", limit=500, since="2026-05-01")

    assert len(out) == 1
    # Unauthenticated path must hit the public AppView.
    assert calls[0][0] == bluesky.PUBLIC_SEARCH_URL
    assert calls[0][0] == bluesky.API_URL  # back-compat alias
    assert calls[0][1]["q"] == "flood"
    assert calls[0][1]["limit"] == 100
    assert calls[0][1]["sort"] == "latest"
    assert calls[0][1]["since"] == "2026-05-01"
    assert calls[0][2] is None


def test_fetch_search_results_uses_auth_headers_when_configured(monkeypatch):
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append((url, headers))
        return FakeResponse({"posts": []})

    monkeypatch.setattr(bluesky, "_auth_headers", lambda: {"Authorization": "Bearer token"})
    monkeypatch.setattr(bluesky.requests, "get", fake_get)

    bluesky._fetch_search_results("flood")

    # Authenticated path must hit the bsky.social PDS, not the public AppView,
    # because public.api.bsky.app does not accept Bearer tokens.
    assert calls == [(bluesky.AUTHED_SEARCH_URL, {"Authorization": "Bearer token"})]


def test_auth_headers_create_session_from_env(monkeypatch):
    bluesky._ACCESS_JWT = None
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json))
        return FakeResponse({"accessJwt": "jwt-token"})

    monkeypatch.setenv("BLUESKY_HANDLE", "user.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-password")
    monkeypatch.setattr(bluesky.requests, "post", fake_post)

    assert bluesky._auth_headers() == {"Authorization": "Bearer jwt-token"}
    assert calls == [
        (
            bluesky.AUTH_URL,
            {"identifier": "user.bsky.social", "password": "app-password"},
        )
    ]
    assert bluesky._auth_headers() == {"Authorization": "Bearer jwt-token"}
    bluesky._ACCESS_JWT = None


def test_fetch_search_results_reports_auth_rejection(monkeypatch):
    monkeypatch.setattr(
        bluesky.requests,
        "get",
        lambda url, params, headers, timeout: FakeResponse(status_code=401),
    )
    monkeypatch.setattr(bluesky, "_auth_headers", lambda: None)

    with pytest.raises(requests.HTTPError, match="Bluesky search rejected"):
        bluesky._fetch_search_results("flood")


def test_fetch_search_results_refreshes_token_on_401_and_retries(monkeypatch):
    bluesky._ACCESS_JWT = "stale-token"
    monkeypatch.setenv("BLUESKY_HANDLE", "user.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-password")

    # First the cached "stale" token is rejected; after createSession returns
    # a fresh JWT the retry succeeds.
    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer stale-token":
            return FakeResponse(status_code=401)
        return FakeResponse({"posts": [_post("at://did/app.bsky.feed.post/1", "flood")]})

    def fake_post(url, json, timeout):
        return FakeResponse({"accessJwt": "fresh-token"})

    monkeypatch.setattr(bluesky.requests, "get", fake_get)
    monkeypatch.setattr(bluesky.requests, "post", fake_post)

    out = bluesky._fetch_search_results("flood")

    assert len(out) == 1
    assert calls == ["Bearer stale-token", "Bearer fresh-token"]
    assert bluesky._ACCESS_JWT == "fresh-token"
    bluesky._reset_access_token()


def test_fetch_search_results_gives_up_when_refresh_also_401(monkeypatch):
    bluesky._ACCESS_JWT = "stale-token"
    monkeypatch.setenv("BLUESKY_HANDLE", "user.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-password")

    monkeypatch.setattr(
        bluesky.requests,
        "get",
        lambda url, params, headers, timeout: FakeResponse(status_code=401),
    )
    monkeypatch.setattr(
        bluesky.requests,
        "post",
        lambda url, json, timeout: FakeResponse({"accessJwt": "fresh-token"}),
    )

    with pytest.raises(requests.HTTPError, match="Bluesky search rejected"):
        bluesky._fetch_search_results("flood")
    bluesky._reset_access_token()


def test_search_url_switches_with_auth():
    assert bluesky._search_url(None) == bluesky.PUBLIC_SEARCH_URL
    assert bluesky._search_url({"Authorization": "Bearer x"}) == bluesky.AUTHED_SEARCH_URL


def test_bluesky_credentials_strip_leading_at_and_whitespace(monkeypatch):
    # The Bluesky UI shows handles with a leading ``@`` which operators tend
    # to copy verbatim, but ``createSession`` rejects that form. Confirm we
    # normalize the env value before sending it to the API.
    monkeypatch.setenv("BLUESKY_HANDLE", "  @user.bsky.social  ")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "  abcd-efgh-ijkl-mnop  ")
    assert bluesky._bluesky_credentials() == (
        "user.bsky.social",
        "abcd-efgh-ijkl-mnop",
    )


def test_bluesky_credentials_treat_empty_or_at_only_as_missing(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "@")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "secret")
    assert bluesky._bluesky_credentials() == (None, None)

    monkeypatch.setenv("BLUESKY_HANDLE", "user.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "   ")
    assert bluesky._bluesky_credentials() == (None, None)


def test_post_to_record_normalizes_matching_post():
    record = bluesky._post_to_record(
        _post("at://did/app.bsky.feed.post/abc123", "Flash flood near the river"),
        ["flash flood", "flood"],
    )

    assert record is not None
    assert record["platform"] == "bluesky"
    assert record["post_id"] == "at://did/app.bsky.feed.post/abc123"
    assert record["created_at"] == "2026-05-13T10:00:00Z"
    assert record["language"] == "en"
    assert record["url"] == "https://bsky.app/profile/user.bsky.social/post/abc123"
    assert record["matched_keywords"] == ["flash flood", "flood"]
    assert "river" in record["matched_context_terms"]
    assert "flash flood" in record["matched_strong_terms"]
    assert record["filter_score"] > 0.0
    assert record["filter_reason"] == "flood_keyword_with_context"
    assert record["source_confidence"] == 0.6
    assert record["author_id_hash"]


def test_post_to_record_excludes_false_positive():
    record = bluesky._post_to_record(
        _post("at://did/app.bsky.feed.post/abc123", "A flood of emails arrived"),
        ["flood"],
    )

    assert record is None


def test_post_to_record_excludes_keyword_without_disaster_context():
    record = bluesky._post_to_record(
        _post("at://did/app.bsky.feed.post/abc123", "What a flood of ideas today"),
        ["flood"],
    )

    assert record is None


def test_classify_text_keeps_weak_keyword_when_context_exists():
    out = bluesky._classify_text(
        "Flooding downtown after heavy rain, road closed",
        ["flooding"],
    )

    assert out["keep"] is True
    assert out["matched_keywords"] == ["flooding"]
    assert "downtown" in out["matched_context_terms"]
    assert "rain" in out["matched_context_terms"]
    assert out["filter_score"] > 0.5


def test_classify_text_tracks_exclusion_reason():
    out = bluesky._classify_text("Please flood my inbox", ["flood"])

    assert out["keep"] is False
    assert out["excluded_keywords"] == ["flood my inbox"]
    assert out["filter_reason"] == "excluded_or_no_keyword"


@pytest.mark.parametrize(
    "text",
    [
        "We have to flood Congress to stop this massive grift TODAY",
        "Rove made 'flood the zone with shit' a central part of their strategy",
        "We should be flooding the streets, especially in the states they are hitting hard",
        "Can we flood the portal demanding warnings",
        "Time to flood the polls before they steal it",
        "Flood the courts with amicus briefs",
        "flood the senate switchboard",
        "Flood the airwaves until they listen",
    ],
)
def test_classify_text_rejects_political_metaphors(text):
    out = bluesky._classify_text(text, ["flood", "flooding"])

    assert out["keep"] is False, f"expected political metaphor rejection for: {text}"
    assert any(
        excl.startswith("political_metaphor")
        or excl in bluesky.DEFAULT_EXCLUDED_PHRASES
        for excl in out["excluded_keywords"]
    ), f"missing political metaphor marker for: {text} (got {out['excluded_keywords']})"


@pytest.mark.parametrize(
    "text",
    [
        "Flood Advisory issued May 13 at 4:05PM AKDT until May 14 at 4:00PM AKDT by NWS Anchorage AK",
        "Record flooding pushed Michigan's dams to the brink of disaster",
        "Northern, eastern Spain face floods amid heavy rain",
        "Flash flood warning issued for downtown after heavy rain",
    ],
)
def test_classify_text_keeps_real_flood_news(text):
    out = bluesky._classify_text(text, ["flood", "flooding"])

    assert out["keep"] is True, f"unexpectedly rejected genuine flood text: {text}"
    assert not any(
        excl.startswith("political_metaphor")
        for excl in out["excluded_keywords"]
    ), f"false-positive metaphor match for: {text}"


def test_political_metaphor_pattern_matches_articles_and_plurals():
    # Sanity check the regex directly so any future tweak is visible in diffs.
    assert bluesky._POLITICAL_METAPHOR_PATTERN.search("flood congress")
    assert bluesky._POLITICAL_METAPHOR_PATTERN.search("flooding the senate")
    assert bluesky._POLITICAL_METAPHOR_PATTERN.search("flooded the courts")
    assert bluesky._POLITICAL_METAPHOR_PATTERN.search("flood the white house")
    # Negative: a literal flood near a river must not match.
    assert not bluesky._POLITICAL_METAPHOR_PATTERN.search("flood damaged the river bridge")


def test_fetch_posts_deduplicates_across_keyword_queries(monkeypatch):
    calls = []

    def fake_fetch(query, *, limit, since=None, until=None):
        calls.append((query, limit, since))
        return [_post("at://did/app.bsky.feed.post/same", "flooding downtown")]

    monkeypatch.setattr(bluesky, "_fetch_search_results", fake_fetch)

    # Force lookback_hours=0 so the env-driven default does not inject a
    # ``since`` cutoff into this dedup-focused test.
    out = bluesky.fetch_posts(
        keywords=["flood", "flooding"], max_posts=10, lookback_hours=0
    )

    assert len(out) == 1
    assert out[0]["post_id"] == "at://did/app.bsky.feed.post/same"
    # No lookback configured -> no implicit since cutoff.
    assert calls == [("flood", 10, None), ("flooding", 9, None)]


def test_fetch_posts_applies_lookback_window_as_since(monkeypatch):
    seen_since: list[str | None] = []

    def fake_fetch(query, *, limit, since=None, until=None):
        seen_since.append(since)
        return []

    monkeypatch.setattr(bluesky, "_fetch_search_results", fake_fetch)

    bluesky.fetch_posts(keywords=["flood"], max_posts=1, lookback_hours=6)

    assert seen_since, "expected the fake fetch to be called"
    assert seen_since[0] is not None
    # The cutoff is rendered as an RFC 3339 Z-suffixed timestamp.
    assert seen_since[0].endswith("Z")
    assert "T" in seen_since[0]


def test_fetch_posts_explicit_since_overrides_lookback(monkeypatch):
    seen_since: list[str | None] = []

    def fake_fetch(query, *, limit, since=None, until=None):
        seen_since.append(since)
        return []

    monkeypatch.setattr(bluesky, "_fetch_search_results", fake_fetch)

    bluesky.fetch_posts(
        keywords=["flood"],
        max_posts=1,
        since="2026-05-01T00:00:00Z",
        lookback_hours=24,
    )

    assert seen_since == ["2026-05-01T00:00:00Z"]


def test_run_writes_snapshot_inserts_records_and_logs_success(tmp_path, monkeypatch):
    records = [
        {
            "platform": "bluesky",
            "post_id": "at://did/app.bsky.feed.post/1",
            "created_at": "2026-05-13T10:00:00Z",
            "text": "flooding downtown",
            "matched_keywords": ["flooding"],
            "raw_payload": {},
        }
    ]
    inserted = []
    logs = []

    monkeypatch.setattr(bluesky, "fetch_posts", lambda **kwargs: records)
    monkeypatch.setattr(bluesky, "raw_subdir", lambda slug: tmp_path / slug)
    monkeypatch.setattr(bluesky, "sha256_file", lambda path: "checksum")
    monkeypatch.setattr(
        bluesky,
        "insert_social_media_posts",
        lambda recs, **kwargs: inserted.append((recs, kwargs)) or len(recs),
    )
    monkeypatch.setattr(
        bluesky,
        "log_ingestion",
        lambda **kwargs: logs.append(kwargs),
    )

    out = bluesky.run(max_posts=1, per_query_limit=1, keywords=["flood"])

    assert out == 1
    snapshot = next((tmp_path / "social_media" / "bluesky").glob("bluesky_*.json"))
    assert json.loads(snapshot.read_text(encoding="utf-8")) == records
    assert inserted[0][0] == records
    assert inserted[0][1]["platform"] == "bluesky"
    assert inserted[0][1]["source"] == "Bluesky"
    assert logs[0]["status"] == "success"
    assert logs[0]["rows_ingested"] == 1


def test_run_logs_failure_before_reraising(tmp_path, monkeypatch):
    logs = []

    def fail_fetch(**kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(bluesky, "fetch_posts", fail_fetch)
    monkeypatch.setattr(bluesky, "raw_subdir", lambda slug: Path(tmp_path) / slug)
    monkeypatch.setattr(bluesky, "log_ingestion", lambda **kwargs: logs.append(kwargs))

    with pytest.raises(requests.RequestException, match="network down"):
        bluesky.run(max_posts=1, per_query_limit=1, keywords=["flood"])

    assert logs[0]["status"] == "failure"
    assert "network down" in logs[0]["message"]
