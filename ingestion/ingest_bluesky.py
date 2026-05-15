"""
Ingest public Bluesky posts that mention flood-related terms.

The MVP uses the public Bluesky AppView search endpoint instead of a long-lived
firehose process. That keeps Airflow runs bounded and avoids adding a new
WebSocket/AT Protocol dependency for the first social-media integration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from config.settings import HTTP_TIMEOUT
from db.client import insert_social_media_posts, log_ingestion
from .common import new_batch_id, raw_subdir, sha256_file

logger = logging.getLogger(__name__)

SOURCE = "Bluesky"
PLATFORM = "bluesky"
# Two different hosts for the same lexicon:
#   * ``public.api.bsky.app`` is the unauthenticated AppView. It does NOT
#     accept Bearer tokens; sending one is ignored at best and rejected at
#     worst. Use only when no App Password is configured.
#   * ``bsky.social`` is the default Bluesky PDS. It accepts the access JWT
#     returned by ``createSession`` and proxies authenticated reads to the
#     AppView. Use this whenever we have credentials.
PUBLIC_SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
AUTHED_SEARCH_URL = "https://bsky.social/xrpc/app.bsky.feed.searchPosts"
AUTH_URL = "https://bsky.social/xrpc/com.atproto.server.createSession"
# Back-compat: kept so tests / external imports that referenced ``API_URL``
# continue to resolve. New code should use ``_search_url()``.
API_URL = PUBLIC_SEARCH_URL

DEFAULT_KEYWORDS = (
    "flood",
    "flooding",
    "flash flood",
    "flooded",
    "flood alert",
    "flood warning",
    "flood rescue",
    "flood evacuation",
    "inundation",
    "inundated",
    "river overflow",
    "overflowing river",
    "dam overflow",
    "levee breach",
    "monsoon flood",
    "storm surge",
    "urban flooding",
    "inondation",
    "inondations",
    "alerte inondation",
    "crue",
    "crue soudaine",
    "debordement",
    "d\u00e9bordement",
    "pluies torrentielles",
    "\u0641\u064a\u0636\u0627\u0646\u0627\u062a",
    "\u0641\u064a\u0636\u0627\u0646",
    "\u0633\u064a\u0648\u0644",
    "\u0633\u064a\u0644",
    "\u0627\u0645\u0637\u0627\u0631 \u063a\u0632\u064a\u0631\u0629",
    "\u0623\u0645\u0637\u0627\u0631 \u063a\u0632\u064a\u0631\u0629",
)

STRONG_FLOOD_TERMS = (
    "flash flood",
    "flood alert",
    "flood warning",
    "flood rescue",
    "flood evacuation",
    "inundation",
    "river overflow",
    "overflowing river",
    "dam overflow",
    "levee breach",
    "monsoon flood",
    "storm surge",
    "urban flooding",
    "inondation",
    "inondations",
    "alerte inondation",
    "crue",
    "crue soudaine",
    "d\u00e9bordement",
    "debordement",
    "\u0641\u064a\u0636\u0627\u0646\u0627\u062a",
    "\u0641\u064a\u0636\u0627\u0646",
    "\u0633\u064a\u0648\u0644",
    "\u0633\u064a\u0644",
)

DEFAULT_CONTEXT_TERMS = (
    "evacuat",
    "rescue",
    "shelter",
    "emergency",
    "warning",
    "alert",
    "road",
    "street",
    "bridge",
    "highway",
    "downtown",
    "neighborhood",
    "village",
    "city",
    "river",
    "dam",
    "levee",
    "drainage",
    "water level",
    "rain",
    "heavy rain",
    "storm",
    "monsoon",
    "landslide",
    "power outage",
    "damage",
    "closed",
    "stranded",
    "inondation",
    "secours",
    "urgence",
    "route",
    "pont",
    "rivi\u00e8re",
    "pluie",
    "orage",
    "crue",
    "\u0625\u062e\u0644\u0627\u0621",
    "\u0625\u0646\u0642\u0627\u0630",
    "\u0637\u0648\u0627\u0631\u0626",
    "\u0637\u0631\u064a\u0642",
    "\u062c\u0633\u0631",
    "\u0648\u0627\u062f\u064a",
    "\u0623\u0645\u0637\u0627\u0631",
    "\u0627\u0645\u0637\u0627\u0631",
    "\u0623\u0636\u0631\u0627\u0631",
)

DEFAULT_EXCLUDED_PHRASES = (
    "flood of emails",
    "flood my inbox",
    "flood the market",
    "flooded with messages",
    "flood of messages",
    "flood of notifications",
    "flood of requests",
    "flood the comments",
    "flood this post",
    "flood my mentions",
    "flooded with work",
    "flooded with calls",
    "flood of tears",
    "flood of memories",
    "flood of information",
    "flood of data",
    "flood the timeline",
    "flooded timeline",
    "newsfeed is inundated",
    "feed is inundated",
    "inundated with stories",
    "inundated with posts",
    "inundated with reposts",
    "inundated with triplicates",
    "avoir crue",
    "j'ai crue",
    "j\u2019ai crue",
    "market flood",
    "flood sale",
    "flood insurance ad",
    # Political-protest metaphors observed in production. The first
    # 50-row Bluesky sample contained several of these, e.g.:
    #   "we have to flood Congress to stop this..."
    #   "flood the zone with shit"
    #   "We should be flooding the streets"
    # See docs/social_media_ingestion/step_13_production_cutover.md.
    "flood the zone",
    "flood the portal",
    "flood the streets",
    "flooding the streets",
    "flooded the streets",
    "flood the system",
    "flood the airwaves",
    "flood the polls",
    "flood the courts",
    "flood the senate",
    "flood the white house",
    "flood the capitol",
    "flood congress",
    "flood the hill",
)

# Pattern-based safety net for the same family of political metaphors so
# small spelling/article variants still get caught (e.g. "flooding Congress",
# "flood up the courts"). The verb form is intentionally restricted to the
# three weak English forms — strong terms like "flash flood" stay unaffected.
_POLITICAL_METAPHOR_PATTERN = re.compile(
    r"\bflood(?:ing|ed)?\s+(?:the\s+|up\s+the\s+)?"
    r"(?:zone|portal|inbox|congress|senate|"
    r"white\s+house|polls?|courts?|streets?|airwaves|"
    r"system|government|hill|capitol|"
    r"comments?|timeline|mentions?|feed|"
    r"chat|chats|dms?|replies?)\b",
    re.IGNORECASE,
)

_INUNDATED_METAPHOR_PATTERN = re.compile(
    r"\binundated\s+(?:with|in|by)\s+"
    r"(?:stories|posts?|reposts?|messages?|emails?|notifications?|"
    r"requests?|information|data|news|updates?|content|work|calls?|"
    r"triplicates?|olds|visitors?|tourists?|fans|customers?|crowds?)\b",
    re.IGNORECASE,
)

_CROWD_STREETS_METAPHOR_PATTERN = re.compile(
    r"(?:fans?|crowds?|tourists?|visitors?).{0,80}"
    r"\bflood(?:ing|ed)?\s+(?:downtown\s+)?streets?\b|"
    r"\bflood(?:ing|ed)?\s+(?:downtown\s+)?streets?\s+ahead\s+of\b",
    re.IGNORECASE,
)

DEFAULT_MAX_POSTS = int(os.getenv("BLUESKY_MAX_POSTS", "100"))
DEFAULT_PER_QUERY_LIMIT = int(os.getenv("BLUESKY_PER_QUERY_LIMIT", "25"))
# Optional lookback window. When set, every run only fetches posts created in
# the last N hours. Keeps scheduled runs bounded and reduces duplicate work
# across days. ``0`` (the default) disables the implicit window so callers can
# still pass explicit ``since`` / ``until`` values.
DEFAULT_LOOKBACK_HOURS = int(os.getenv("BLUESKY_LOOKBACK_HOURS", "0"))
_ACCESS_JWT: str | None = None


def _env_list(name: str, default: tuple[str, ...]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _bluesky_credentials() -> tuple[str | None, str | None]:
    """Read and normalize Bluesky credentials from the environment.

    Bluesky's ``createSession`` expects the bare handle (``user.bsky.social``)
    and rejects the leading ``@`` form (``@user.bsky.social``). Operators
    naturally copy handles with the ``@`` prefix from the Bluesky UI, so we
    strip it defensively rather than failing on what is otherwise valid input.
    Whitespace from copy/paste is also trimmed.
    """
    handle = os.getenv("BLUESKY_HANDLE")
    app_password = os.getenv("BLUESKY_APP_PASSWORD")
    if handle:
        handle = handle.strip().lstrip("@") or None
    if app_password:
        app_password = app_password.strip() or None
    if handle and app_password:
        return handle, app_password
    return None, None


def _reset_access_token() -> None:
    """Forget the cached JWT so the next ``_auth_headers()`` call re-logs in.

    Exposed at module scope so tests and the 401-retry path can both invalidate
    a stale token without touching the global directly.
    """
    global _ACCESS_JWT
    _ACCESS_JWT = None


def _auth_headers() -> dict[str, str] | None:
    """Return authenticated Bluesky headers when app credentials are configured."""
    global _ACCESS_JWT
    if _ACCESS_JWT:
        return {"Authorization": f"Bearer {_ACCESS_JWT}"}

    handle, app_password = _bluesky_credentials()
    if not handle or not app_password:
        return None

    response = requests.post(
        AUTH_URL,
        json={"identifier": handle, "password": app_password},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("accessJwt")
    if not token:
        raise requests.HTTPError("Bluesky authentication succeeded without accessJwt")
    _ACCESS_JWT = token
    return {"Authorization": f"Bearer {_ACCESS_JWT}"}


def _search_url(headers: dict[str, str] | None) -> str:
    """Pick the right host for the search request.

    When we authenticated, we must hit the PDS (``bsky.social``) — the
    ``public.api.bsky.app`` AppView is intentionally unauthenticated and will
    reject (or ignore) Bearer tokens, which is the most common reason production
    runs stay stuck on 401/403 even after App Password credentials are set.
    """
    return AUTHED_SEARCH_URL if headers else PUBLIC_SEARCH_URL


def _matched_keywords(text: str, keywords: list[str]) -> list[str]:
    folded = text.casefold()
    return [kw for kw in keywords if kw.casefold() in folded]


def _matched_context_terms(text: str) -> list[str]:
    folded = text.casefold()
    return [term for term in DEFAULT_CONTEXT_TERMS if term.casefold() in folded]


def _matched_strong_terms(text: str) -> list[str]:
    folded = text.casefold()
    return [term for term in STRONG_FLOOD_TERMS if term.casefold() in folded]


def _excluded_keywords(text: str) -> list[str]:
    """Return every exclusion phrase / metaphor pattern that matches the text.

    Combines the literal phrase list (fast, exact match) with one regex pass
    that catches the broader ``flood [the] <institution>`` political-metaphor
    family. The regex match is reported under a synthetic phrase name so
    downstream traceability (``excluded_keywords`` in the raw payload) still
    explains which rule rejected the post.
    """
    folded = text.casefold()
    matches = [phrase for phrase in DEFAULT_EXCLUDED_PHRASES if phrase in folded]
    if _POLITICAL_METAPHOR_PATTERN.search(text):
        marker = "political_metaphor:flood_the_<institution>"
        if marker not in matches:
            matches.append(marker)
    if _INUNDATED_METAPHOR_PATTERN.search(text):
        marker = "metaphor:inundated_with_content"
        if marker not in matches:
            matches.append(marker)
    if _CROWD_STREETS_METAPHOR_PATTERN.search(text):
        marker = "metaphor:crowd_flooding_streets"
        if marker not in matches:
            matches.append(marker)
    return matches


def _classify_text(text: str, keywords: list[str]) -> dict[str, Any]:
    matches = _matched_keywords(text, keywords)
    context_terms = _matched_context_terms(text)
    strong_terms = _matched_strong_terms(text)
    excluded = _excluded_keywords(text)
    if not text or excluded or not matches:
        return {
            "keep": False,
            "matched_keywords": matches,
            "matched_context_terms": context_terms,
            "matched_strong_terms": strong_terms,
            "excluded_keywords": excluded,
            "filter_score": 0.0,
            "filter_reason": "excluded_or_no_keyword",
        }

    has_hashtag_signal = any(tag in text.casefold() for tag in ("#flood", "#flooding"))
    enough_context = bool(strong_terms or context_terms or has_hashtag_signal)
    if not enough_context:
        return {
            "keep": False,
            "matched_keywords": matches,
            "matched_context_terms": context_terms,
            "matched_strong_terms": strong_terms,
            "excluded_keywords": excluded,
            "filter_score": 0.0,
            "filter_reason": "keyword_without_disaster_context",
        }

    score = 0.45
    score += min(len(matches), 4) * 0.08
    score += min(len(context_terms), 5) * 0.05
    if strong_terms:
        score += 0.18
    if has_hashtag_signal:
        score += 0.05

    return {
        "keep": True,
        "matched_keywords": matches,
        "matched_context_terms": context_terms,
        "matched_strong_terms": strong_terms,
        "excluded_keywords": excluded,
        "filter_score": min(1.0, score),
        "filter_reason": "flood_keyword_with_context",
    }


def _author_hash(author: dict[str, Any]) -> str | None:
    identifier = author.get("did") or author.get("handle")
    if not identifier:
        return None
    return hashlib.sha256(str(identifier).encode("utf-8")).hexdigest()


def _post_url(post: dict[str, Any]) -> str | None:
    uri = post.get("uri")
    author = post.get("author") or {}
    handle = author.get("handle")
    if not uri or not handle:
        return None
    rkey = str(uri).rsplit("/", 1)[-1]
    if not rkey:
        return None
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def _post_to_record(post: dict[str, Any], keywords: list[str]) -> dict[str, Any] | None:
    record = post.get("record") or {}
    text = record.get("text") or ""
    classification = _classify_text(text, keywords)
    post_id = post.get("uri")
    created_at = record.get("createdAt") or post.get("indexedAt")

    if not post_id or not created_at or not classification["keep"]:
        return None

    author = post.get("author") or {}
    langs = record.get("langs") or []
    language = langs[0] if langs else None

    return {
        "platform": PLATFORM,
        "post_id": post_id,
        "created_at": created_at,
        "indexed_at": post.get("indexedAt"),
        "text": text,
        "language": language,
        "url": _post_url(post),
        "author_id_hash": _author_hash(author),
        "place_name": None,
        "country": None,
        "latitude": None,
        "longitude": None,
        "matched_keywords": classification["matched_keywords"],
        "matched_context_terms": classification["matched_context_terms"],
        "matched_strong_terms": classification["matched_strong_terms"],
        "excluded_keywords": classification["excluded_keywords"],
        "filter_score": classification["filter_score"],
        "filter_reason": classification["filter_reason"],
        "source_confidence": 0.6,
        "raw_payload": post,
    }


def _fetch_search_results(
    query: str,
    *,
    limit: int = DEFAULT_PER_QUERY_LIMIT,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "q": query,
        "limit": max(1, min(limit, 100)),
        "sort": "latest",
    }
    if since:
        params["since"] = since
    if until:
        params["until"] = until

    headers = _auth_headers()
    response = requests.get(
        _search_url(headers), params=params, headers=headers, timeout=HTTP_TIMEOUT
    )

    # If the server rejects an authenticated request, the most likely cause is
    # that the access JWT cached in this process has expired (access JWTs from
    # ``createSession`` live ~2 hours). Drop the token and retry exactly once
    # with a fresh login before giving up.
    if response.status_code in {401, 403} and headers is not None:
        logger.info("Bluesky 401/403 with cached JWT; refreshing session and retrying once")
        _reset_access_token()
        headers = _auth_headers()
        if headers is not None:
            response = requests.get(
                _search_url(headers),
                params=params,
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )

    if response.status_code in {401, 403}:
        raise requests.HTTPError(
            "Bluesky search rejected the request (HTTP "
            f"{response.status_code}). Public search is increasingly "
            "rate-limited or blocked for unauthenticated clients. Set "
            "BLUESKY_HANDLE and BLUESKY_APP_PASSWORD in .env (use a Bluesky "
            "App Password, NOT your account password) so the ingester can "
            "use the authenticated PDS at bsky.social.",
            response=response,
        )
    response.raise_for_status()
    payload = response.json()
    return payload.get("posts", []) or []


def _default_since(lookback_hours: int) -> str | None:
    """Translate a lookback-hours window into the ISO timestamp Bluesky expects."""
    if lookback_hours <= 0:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    # Bluesky accepts RFC 3339 / ISO 8601 with a trailing ``Z``.
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_posts(
    *,
    keywords: list[str] | None = None,
    max_posts: int = DEFAULT_MAX_POSTS,
    per_query_limit: int = DEFAULT_PER_QUERY_LIMIT,
    since: str | None = None,
    until: str | None = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> list[dict[str, Any]]:
    """Fetch and normalize bounded Bluesky search results.

    When ``since`` is not supplied but ``lookback_hours`` is positive, the
    function derives a ``since`` cutoff so each run focuses on recent posts.
    Explicit ``since`` always wins over the implicit window.
    """
    selected_keywords = keywords or _env_list("SOCIAL_MEDIA_KEYWORDS", DEFAULT_KEYWORDS)
    effective_since = since or _default_since(lookback_hours)
    records_by_id: dict[str, dict[str, Any]] = {}

    for keyword in selected_keywords:
        if len(records_by_id) >= max_posts:
            break
        posts = _fetch_search_results(
            keyword,
            limit=min(per_query_limit, max_posts - len(records_by_id)),
            since=effective_since,
            until=until,
        )
        for post in posts:
            record = _post_to_record(post, selected_keywords)
            if record:
                records_by_id.setdefault(record["post_id"], record)
            if len(records_by_id) >= max_posts:
                break

    return list(records_by_id.values())


def run(
    *,
    max_posts: int = DEFAULT_MAX_POSTS,
    per_query_limit: int = DEFAULT_PER_QUERY_LIMIT,
    keywords: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> int:
    started_at = datetime.now(timezone.utc)
    batch_id = new_batch_id("bluesky")
    target_dir = raw_subdir("social_media/bluesky")
    target_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = target_dir / f"bluesky_{batch_id}.json"
    # Record the host actually used so the audit log distinguishes the
    # authenticated PDS path from the public AppView path.
    source_url = AUTHED_SEARCH_URL if _bluesky_credentials()[0] else PUBLIC_SEARCH_URL

    try:
        records = fetch_posts(
            keywords=keywords,
            max_posts=max_posts,
            per_query_limit=per_query_limit,
            since=since,
            until=until,
            lookback_hours=lookback_hours,
        )
        snapshot_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        file_checksum = sha256_file(snapshot_path)
        rows = insert_social_media_posts(
            records,
            platform=PLATFORM,
            source=SOURCE,
            source_url=source_url,
            file_path=str(snapshot_path),
            batch_id=batch_id,
        )
        log_ingestion(
            batch_id=batch_id,
            source=SOURCE,
            status="success",
            rows_ingested=rows,
            source_url=source_url,
            file_path=str(snapshot_path),
            file_checksum=file_checksum,
            started_at=started_at,
        )
        return rows
    except (requests.RequestException, ValueError, OSError) as exc:
        log_ingestion(
            batch_id=batch_id,
            source=SOURCE,
            status="failure",
            source_url=source_url,
            file_path=str(snapshot_path),
            message=str(exc),
            started_at=started_at,
        )
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    n = run()
    logger.info("Bluesky ingestion complete (%s rows).", n)
