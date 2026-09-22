import json

import config
from tools.web_search import make_source_id, normalize_url, search_web, to_reference


class FakeTavilyClient:
    def __init__(self, response=None, error=None):
        self.response = {"results": []} if response is None else response
        self.error = error
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _result(url="https://example.com/article", date="2025-05-01"):
    return {
        "url": url,
        "title": "Example",
        "content": "A source-backed result.",
        "published_date": date,
    }


def _configure(monkeypatch, tmp_path, *, use_cache=False):
    monkeypatch.setattr(config, "SEARCH_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "USE_SEARCH_CACHE", use_cache)
    monkeypatch.setattr(config, "SEARCH_EXCLUDE_DOMAINS", [])
    monkeypatch.setattr(config, "SEARCH_MIN_DATE", "")


def test_same_url_has_same_source_id():
    first = "https://EXAMPLE.com:443/path/?b=2&utm_source=x&a=1#fragment"
    second = "https://example.com/path?a=1&b=2"
    assert make_source_id(first) == make_source_id(second)


def test_url_normalization_removes_tracking_fragment_and_trailing_slash():
    assert normalize_url("https://Example.com:443/a/?utm_medium=x&b=2&a=1#x") == (
        "https://example.com/a?a=1&b=2"
    )


def test_to_reference_excludes_content():
    record = {
        "source_id": "web:abc",
        "kind": "web",
        "author": "",
        "date": "",
        "title": "Title",
        "venue": "example.com",
        "url": "https://example.com",
        "used_by": ["market"],
        "stance": "neutral",
        "content": "not part of Reference",
    }
    reference = to_reference(record)
    assert "content" not in reference
    assert len(reference) == 9


def test_tavily_exception_returns_empty(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    client = FakeTavilyClient(error=RuntimeError("secret provider detail"))
    results = search_web("query", "neutral", client=client)
    assert results == []
    assert results.error_code == "E-1002"


def test_zero_results_returns_empty(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    results = search_web("query", "neutral", client=FakeTavilyClient())
    assert results == []
    assert results.error_code is None


def test_invalid_provider_response_is_diagnostic_failure(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    results = search_web("query", "neutral", client=FakeTavilyClient({"unexpected": []}))
    assert results == []
    assert results.error_code == "E-1002"


def test_live_search_saves_cache_without_secret(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    client = FakeTavilyClient({"results": [_result()]})
    assert search_web("query", "positive", client=client, api_key="do-not-store")
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    cached = files[0].read_text(encoding="utf-8")
    assert "do-not-store" not in cached
    assert json.loads(cached)["search_depth"] == config.WEB_SEARCH_DEPTH


def test_cache_reused_when_enabled(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    first = FakeTavilyClient({"results": [_result()]})
    search_web("same request", "positive", client=first)
    monkeypatch.setattr(config, "USE_SEARCH_CACHE", True)
    second = FakeTavilyClient(error=AssertionError("live call must not happen"))
    records = search_web("same request", "positive", client=second)
    assert records and second.calls == []


def test_cache_ignored_when_disabled(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    search_web("same request", "positive", client=FakeTavilyClient({"results": [_result()]}))
    live = FakeTavilyClient({"results": [_result("https://example.org/live")]})
    records = search_web("same request", "positive", client=live)
    assert len(live.calls) == 1
    assert records[0]["url"] == "https://example.org/live"


def test_excluded_domain_filter(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "SEARCH_EXCLUDE_DOMAINS", ["blocked.example"])
    client = FakeTavilyClient(
        {"results": [_result("https://sub.blocked.example/a"), _result("https://allowed.example/a")]}
    )
    records = search_web("query", "neutral", client=client)
    assert [record["url"] for record in records] == ["https://allowed.example/a"]


def test_min_date_filter(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "SEARCH_MIN_DATE", "2025-01-01")
    client = FakeTavilyClient(
        {
            "results": [
                _result("https://example.com/old", "2024-12-31"),
                _result("https://example.com/new", "2025-01-02"),
                _result("https://example.com/unknown", ""),
            ]
        }
    )
    records = search_web("query", "neutral", client=client)
    assert [record["url"] for record in records] == ["https://example.com/new"]


def test_injected_client_prevents_real_tavily_call(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    client = FakeTavilyClient({"results": [_result()]})
    records = search_web("offline", "neutral", client=client)
    assert records
    assert client.calls[0]["search_depth"] == config.WEB_SEARCH_DEPTH
    assert client.calls[0]["max_results"] == config.WEB_SEARCH_MAX_RESULTS
