"""Tavily web-search helpers with stable, URL-based source identifiers.

Search results contain the nine :class:`state.Reference` fields plus ``content``.
Live responses are cached after every successful call; cached responses are read
only when ``config.USE_SEARCH_CACHE`` is enabled.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import config
from state import Reference, Stance

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


class SearchResults(list[dict[str, Any]]):
    """List-compatible search results with a non-sensitive failure diagnostic."""

    def __init__(self, values: Iterable[dict[str, Any]] = (), *, error_code: str | None = None):
        super().__init__(values)
        self.error_code = error_code


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


def _normalize_used_by(used_by: Union[str, Sequence[str]]) -> list[str]:
    if isinstance(used_by, str):
        values: Iterable[str] = [used_by]
    else:
        values = used_by
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def result_to_reference(
    result: Mapping[str, Any],
    stance: Stance,
    used_by: Union[str, Sequence[str]] = "market",
) -> Reference:
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
        "title": str(result.get("title") or "").strip(),
        "venue": str(venue).strip(),
        "url": normalized,
        "used_by": _normalize_used_by(used_by),
        "stance": stance,
    }


def to_reference(result: Mapping[str, Any]) -> Reference:
    """Strip search-only fields and return a State-compatible Reference dict."""

    missing = [field for field in _REFERENCE_FIELDS if field not in result]
    if missing:
        raise ValueError(f"Search result is missing Reference fields: {', '.join(missing)}")
    return {field: result[field] for field in _REFERENCE_FIELDS}  # type: ignore[return-value]


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


def _cache_path(query: str, stance: Stance, max_results: int) -> Path:
    payload = {
        "query": query.strip(),
        "stance": stance,
        "max_results": max_results,
        "search_depth": config.WEB_SEARCH_DEPTH,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return Path(config.SEARCH_CACHE_DIR) / f"{digest}.json"


def _load_cache(path: Path) -> list[Mapping[str, Any]] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    results = payload.get("results") if isinstance(payload, Mapping) else None
    if not isinstance(results, list):
        return None
    return [result for result in results if isinstance(result, Mapping)]


def _save_cache(
    path: Path,
    query: str,
    stance: Stance,
    max_results: int,
    results: list[Mapping[str, Any]],
) -> None:
    """Store only request metadata and Tavily results, never credentials."""

    payload = {
        "query": query.strip(),
        "stance": stance,
        "max_results": max_results,
        "search_depth": config.WEB_SEARCH_DEPTH,
        "results": [dict(result) for result in results],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError:
        logging.warning("[E-1002] 웹 검색 캐시 저장 실패")


def _is_excluded(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    for excluded in config.SEARCH_EXCLUDE_DOMAINS:
        domain = str(excluded).lower().strip().lstrip(".").rstrip(".")
        if domain and (hostname == domain or hostname.endswith(f".{domain}")):
            return True
    return False


def _date_key(value: Any) -> tuple[int, int, int] | None:
    match = re.match(r"^\s*(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(value or ""))
    if not match:
        return None
    year, month, day = match.groups()
    try:
        return int(year), int(month or 1), int(day or 1)
    except ValueError:
        return None


def _passes_min_date(result: Mapping[str, Any]) -> bool:
    if not config.SEARCH_MIN_DATE:
        return True
    minimum = _date_key(config.SEARCH_MIN_DATE)
    published = _date_key(
        result.get("published_date")
        or result.get("published_at")
        or result.get("date")
    )
    return minimum is not None and published is not None and published >= minimum


def _raw_results(response: Any) -> list[Mapping[str, Any]] | None:
    if isinstance(response, Mapping):
        results = response.get("results")
    elif isinstance(response, list):
        results = response
    else:
        return None
    if not isinstance(results, list) or any(not isinstance(result, Mapping) for result in results):
        return None
    return results


def search_web(
    query: str,
    stance: Stance,
    max_results: int = config.WEB_SEARCH_MAX_RESULTS,
    used_by: Union[str, Sequence[str]] = "market",
    *,
    client: Any = None,
    api_key: Optional[str] = None,
) -> SearchResults:
    """Search Tavily and return de-duplicated, Reference-compatible records.

    ``client`` is injectable so tests do not require network access or an API
    key. External failures are contained and return an empty list so a graph run
    can continue. Invalid caller arguments still raise ``ValueError``.
    """

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if stance not in _VALID_STANCES:
        raise ValueError(f"Invalid stance: {stance!r}")
    if not isinstance(max_results, int) or max_results < 1:
        raise ValueError("max_results must be a positive integer")

    path = _cache_path(query, stance, max_results)
    raw_results = _load_cache(path) if config.USE_SEARCH_CACHE else None
    if raw_results is None:
        try:
            tavily_client = client if client is not None else _create_tavily_client(api_key)
            response = tavily_client.search(
                query=query.strip(),
                max_results=max_results,
                search_depth=config.WEB_SEARCH_DEPTH,
                include_answer=False,
            )
            raw_results = _raw_results(response)
            if raw_results is None:
                logging.warning("[E-1002] 웹 검색 응답 형식 오류")
                return SearchResults(error_code="E-1002")
            _save_cache(path, query, stance, max_results, raw_results)
        except Exception:  # noqa: BLE001 - public search boundary contains provider failures
            logging.warning("[E-1002] 웹 검색 호출 실패")
            return SearchResults(error_code="E-1002")

    unique: dict[str, dict[str, Any]] = {}
    for raw_result in raw_results:
        raw_url = str(raw_result.get("url") or "")
        try:
            normalized_url = normalize_url(raw_url)
        except (TypeError, ValueError):
            continue
        if _is_excluded(normalized_url) or not _passes_min_date(raw_result):
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

    return SearchResults(unique.values())
