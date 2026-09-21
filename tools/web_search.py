"""Tavily web-search helpers with stable, URL-based source identifiers.

The module intentionally returns plain dictionaries so it can be used before the
project's shared ``state.py`` is available.  Search results include the Reference
fields plus a ``content`` field; callers should use :func:`to_reference` before
placing them in State.references.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


Stance = Literal["positive", "negative", "neutral"]

_VALID_STANCES = {"positive", "negative", "neutral"}
_TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "ref_src",
}
_REFERENCE_FIELDS = (
    "source_id",
    "kind",
    "author",
    "date",
    "title",
    "venue",
    "url",
    "used_by",
    "stance",
)


class WebSearchError(RuntimeError):
    """Raised when a web search cannot be completed or parsed."""


class MissingTavilyAPIKeyError(WebSearchError):
    """Raised when live search is requested without Tavily credentials."""


def normalize_url(url: str) -> str:
    """Return a stable URL representation suitable for hashing.

    Fragments and common tracking parameters are removed, the scheme and host
    are lower-cased, default ports are dropped, query parameters are sorted, and
    non-root trailing slashes are removed.
    """

    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string")

    candidate = url.strip()
    if "://" not in candidate:
        candidate = "https://" + candidate

    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Malformed URL: {url!r}") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError(f"Malformed HTTP(S) URL: {url!r}")
    if any(character.isspace() for character in hostname):
        raise ValueError(f"Malformed URL host: {url!r}")

    host = hostname.lower().rstrip(".")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    path = parsed.path or ""
    if path == "/":
        path = ""
    else:
        path = path.rstrip("/")

    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_PARAMETERS:
            continue
        filtered_query.append((key, value))
    filtered_query.sort(key=lambda item: (item[0], item[1]))

    return urlunsplit((scheme, host, path, urlencode(filtered_query, doseq=True), ""))


def make_source_id(url: str) -> str:
    """Create the stable ``web:<sha1-prefix>`` identifier required by State."""

    normalized = normalize_url(url)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
    return f"web:{digest}"


def _normalize_used_by(used_by: Union[str, Sequence[str]]) -> List[str]:
    if isinstance(used_by, str):
        values: Iterable[str] = [used_by]
    else:
        values = used_by
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def result_to_reference(
    result: Mapping[str, Any],
    stance: Stance,
    used_by: Union[str, Sequence[str]] = "market",
) -> Dict[str, Any]:
    """Convert one Tavily-like result into the agreed Reference dictionary."""

    if stance not in _VALID_STANCES:
        raise ValueError(f"Invalid stance: {stance!r}")

    normalized = normalize_url(str(result.get("url", "")))
    hostname = urlsplit(normalized).hostname or ""
    venue = result.get("venue") or result.get("site_name") or result.get("source") or hostname
    author = result.get("author") or result.get("organization") or ""
    published = (
        result.get("published_date")
        or result.get("published_at")
        or result.get("date")
        or ""
    )

    return {
        "source_id": make_source_id(normalized),
        "kind": "web",
        "author": str(author).strip(),
        "date": str(published).strip(),
        "title": str(result.get("title") or "Untitled web source").strip(),
        "venue": str(venue).strip(),
        "url": normalized,
        "used_by": _normalize_used_by(used_by),
        "stance": stance,
    }


def to_reference(result: Mapping[str, Any]) -> Dict[str, Any]:
    """Strip search-only fields and return a State-compatible Reference dict."""

    missing = [field for field in _REFERENCE_FIELDS if field not in result]
    if missing:
        raise ValueError(f"Search result is missing Reference fields: {', '.join(missing)}")
    return {field: result[field] for field in _REFERENCE_FIELDS}


def _content_from_result(result: Mapping[str, Any]) -> str:
    value = (
        result.get("content")
        or result.get("summary")
        or result.get("snippet")
        or result.get("raw_content")
        or ""
    )
    return " ".join(str(value).split())


def _create_tavily_client(api_key: Optional[str] = None) -> Any:
    key = api_key or os.getenv("TAVILY_API_KEY")
    if not key:
        raise MissingTavilyAPIKeyError(
            "TAVILY_API_KEY is not set. Configure it for live search or inject a "
            "mock Tavily client/search function for local tests."
        )
    try:
        from tavily import TavilyClient
    except ImportError as exc:
        raise WebSearchError(
            "The 'tavily-python' package is required for live web search."
        ) from exc
    return TavilyClient(api_key=key)


def search_web(
    query: str,
    stance: Stance,
    max_results: int = 5,
    used_by: Union[str, Sequence[str]] = "market",
    *,
    client: Any = None,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search Tavily and return de-duplicated, Reference-compatible records.

    ``client`` is injectable so tests do not require network access or an API
    key. Malformed result URLs are skipped; a malformed top-level response or a
    Tavily call failure raises :class:`WebSearchError` with the query included.
    """

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if stance not in _VALID_STANCES:
        raise ValueError(f"Invalid stance: {stance!r}")
    if not isinstance(max_results, int) or max_results < 1:
        raise ValueError("max_results must be a positive integer")

    tavily_client = client if client is not None else _create_tavily_client(api_key)
    try:
        response = tavily_client.search(
            query=query.strip(),
            max_results=max_results,
            search_depth="advanced",
            include_answer=False,
        )
    except Exception as exc:
        raise WebSearchError(f"Tavily search failed for query {query!r}: {exc}") from exc

    if isinstance(response, Mapping):
        raw_results = response.get("results", [])
    elif isinstance(response, list):
        raw_results = response
    else:
        raise WebSearchError(
            f"Unexpected Tavily response type for query {query!r}: "
            f"{type(response).__name__}"
        )
    if not isinstance(raw_results, list):
        raise WebSearchError(f"Tavily response 'results' is not a list for query {query!r}")

    unique: Dict[str, Dict[str, Any]] = {}
    for raw_result in raw_results:
        if not isinstance(raw_result, Mapping):
            continue
        try:
            reference = result_to_reference(raw_result, stance=stance, used_by=used_by)
        except (TypeError, ValueError):
            continue
        enriched = dict(reference)
        enriched["content"] = _content_from_result(raw_result)
        source_id = reference["source_id"]
        existing = unique.get(source_id)
        if existing is None:
            unique[source_id] = enriched
            continue
        if len(enriched["content"]) > len(existing.get("content", "")):
            existing["content"] = enriched["content"]
        for field in ("author", "date", "title", "venue"):
            if not existing.get(field) and enriched.get(field):
                existing[field] = enriched[field]

    return list(unique.values())
