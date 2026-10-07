"""인용·출처 처리 (보고서 출력 계층).

- `Citations`: State의 Evidence가 가리키는 출처만 문서 단위로 병합하고, 본문 등장 순서대로 인용 번호를 매긴다.
- 인용 표기 검사: 챕터 입력에 없던 번호·쪽수 제거, 한 기술만 다루는 문장에 다른 기술 출처가 섞이지 않게 정리.
- REFERENCE 표기 형식 변환과, 게시일이 비어 있는 웹 출처의 게시일 보완.
"""

import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

from state import TECHS, State

RESULT_KEYS = ("tech_summary", "trl_result", "market_result", "stakeholder_result", "domain_result")


def _doc_id(source_id: str) -> str:
    return source_id.split("#")[0]


def evidence_source_ids(data) -> set[str]:
    """State 안의 모든 Evidence가 가리키는 source_id."""
    if isinstance(data, list):
        return {s for v in data for s in evidence_source_ids(v)}
    if isinstance(data, dict):
        if "claim" in data and "source_id" in data:
            return {data["source_id"]}
        return {s for v in data.values() for s in evidence_source_ids(v)}
    return set()


class Citations:
    """Evidence가 가리키는 출처만 문서 단위로 병합하고, 본문 등장 순서대로 인용 번호를 매긴다."""

    def __init__(self, references, used_source_ids):
        used_docs = {_doc_id(s) for s in used_source_ids}
        self.docs = {}
        self.alias: dict[str, str] = {}   # 문서 단위 source_id → REFERENCE 한 줄의 키
        for r in references:
            d = _doc_id(r["source_id"])
            if d not in used_docs:
                continue
            key = _same_doc_key(r) or d
            self.alias[d] = key
            if key in self.docs:
                merged = self.docs[key]["used_by"]
                merged += [u for u in r.get("used_by", []) if u not in merged]
            else:
                self.docs[key] = {**r, "used_by": list(r.get("used_by", []))}
        self.order: list[str] = []

    def cite(self, source_id: str) -> str | None:
        d = self.alias.get(_doc_id(source_id))
        if d is None:
            return None
        if d not in self.order:
            self.order.append(d)
        n = self.order.index(d) + 1
        page = source_id.split("#p")[1] if "#p" in source_id else None
        return f"[{n}, p.{page}]" if page else f"[{n}]"

    def attach(self, data):
        """data 안의 Evidence를 {claim, stance, cite}로 바꾼다 (LLM에는 source_id 대신 인용 번호만 보여줌)."""
        if isinstance(data, list):
            return [self.attach(v) for v in data]
        if not isinstance(data, dict):
            return data
        if "claim" in data and "source_id" in data:
            return {"claim": data["claim"], "stance": data.get("stance"), "cite": self.cite(data["source_id"])}
        return {k: self.attach(v) for k, v in data.items()}

    def reference_lines(self) -> list[str]:
        """본문 인용 순서대로 번호를 매기고, 참고했지만 인용되지 않은 출처는 뒤에 이어 붙인다."""
        order = self.order + [d for d in self.docs if d not in self.order]
        return [f"[{i}] {format_reference(self.docs[d])}" for i, d in enumerate(order, 1)]


def _same_doc_key(r) -> str | None:
    """웹 자료는 www 유무·끝 슬래시만 다른 URL을 같은 문서로 본다 (예: tradingkey.com vs www.tradingkey.com)."""
    if r.get("kind") != "web" or not r.get("url"):
        return None
    m = re.match(r"(?i)^(?:https?://)?(?:www\.)?([^/?#]+)([^?#]*)", r["url"].strip())
    return f"url:{m[1].lower()}{m[2].rstrip('/')}" if m else None


def _date(raw: str, kind: str) -> str:
    """날짜를 표기 형식에 맞춘다: 웹 YYYY-MM-DD, 특허 YYYY-MM, 논문 YYYY. 알 수 없으면 n.d."""
    raw = (raw or "").strip()
    ymd = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    ymd = ymd[0] if ymd else None
    if not ymd:
        try:  # Tavily 등이 주는 RFC 2822 날짜 (예: "Thu, 26 Mar 2026 10:00:00 GMT")
            ymd = parsedate_to_datetime(raw).date().isoformat()
        except (TypeError, ValueError):
            pass
    ym = ymd[:7] if ymd else (re.search(r"\d{4}-\d{2}", raw) or [None])[0]
    year = ymd[:4] if ymd else (re.search(r"\d{4}", raw) or [None])[0]
    value = {"web": ymd, "patent": ym}.get(kind, year)
    return value or year or "n.d."


def _readable_title(title: str, url: str) -> str:
    """태국어 등 폰트에 없는 문자 위주의 제목은 URL 마지막 경로(영문 슬러그)로 대신 쓴다."""
    letters = [c for c in title if c.isalpha()]
    foreign = [c for c in letters if not (c.isascii() or "\uac00" <= c <= "\ud7a3")]
    if letters and len(foreign) > len(letters) / 3:
        slug = re.sub(r"^\d+-", "", url.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")
        return f"{slug} (원문 비영어 제목)"
    return title


def format_reference(r) -> str:
    """노션 가이드 REFERENCE 표기 형식. 작성자가 없으면 기관(사이트)명을 쓴다."""
    v, u = re.sub(r"^www\.", "", r["venue"]), r.get("url", "")
    t = _readable_title(r["title"], u)
    a = (r.get("author") or "").strip() or v
    d = _date(r.get("date", ""), r["kind"])
    if r["kind"] == "patent":
        return f"{a}({d}). {t}, {v}, {u}"
    if r["kind"] == "paper":
        return f"{a}({d}). {t}. {v}."
    return f"{a}({d}). {t}. {v}, {u}"


CITE_GROUP = re.compile(r"\s?\[(\d+(?:,\s*p\.\s*\d+)?(?:\s*[,;]\s*\d+(?:,\s*p\.\s*\d+)?)*)\]")


CITE_ITEM = re.compile(r"(\d+)(?:,\s*p\.\s*(\d+))?")


def _cite_marks(text: str) -> set[tuple[str, str | None]]:
    """"[2] [1, p.7]" → {("2", None), ("1", "7")}"""
    return {(n, p or None) for g in CITE_GROUP.findall(text) for n, p in CITE_ITEM.findall(g)}


def _allowed_cites(data) -> set[tuple[str, str | None]]:
    """입력 JSON의 cite 값에 있는 인용만 허용 목록으로 모은다."""
    if isinstance(data, list):
        return {m for v in data for m in _allowed_cites(v)}
    if isinstance(data, dict):
        own = _cite_marks(data["cite"]) if isinstance(data.get("cite"), str) else set()
        return own | {m for v in data.values() for m in _allowed_cites(v)}
    return set()


def _keep_given_citations(text: str, allowed: set[tuple[str, str | None]]) -> str:
    """입력에 없던 인용(지어낸 번호·쪽수)을 지운다. 쪽수 없는 [n]은 같은 문서가 입력에 있으면 허용."""
    docs = {n for n, _ in allowed}

    def keep(m):
        items = [(n, p or None) for n, p in CITE_ITEM.findall(m[1])]
        ok = [f"[{n}, p.{p}]" if p else f"[{n}]" for n, p in items if (n, p) in allowed or (not p and n in docs)]
        lead = m[0][: len(m[0]) - len(m[0].lstrip())]
        return lead + " ".join(ok) if ok else ""

    return CITE_GROUP.sub(keep, text)


def doc_techs(state: State, cites: Citations) -> dict[str, set[str]]:
    """REFERENCE 한 줄(문서) → 그 문서를 근거로 쓴 기술들."""
    out: dict[str, set[str]] = {}
    for key in RESULT_KEYS:
        for tech, result in (state.get(key) or {}).items():
            for sid in evidence_source_ids(result):
                d = cites.alias.get(_doc_id(sid))
                if d:
                    out.setdefault(d, set()).add(tech)
    return out


SENTENCE = re.compile(r".+?(?:[.!?](?:\s*\[[^\]\n]*\])*(?=\s|$)|$)")


def _keep_same_tech_citations(text: str, cites: Citations, techs_of: dict[str, set[str]]) -> str:
    """한 기술만 다루는 문장에는 그 기술 근거의 인용만 남긴다 (예: TurboQuant 비판 문장에 붙은 InfiniGen 자료 제거)."""
    def fix_sentence(m):
        sent = m[0]
        named = [t for t in TECHS if t.lower() in sent.lower()]
        if len(named) != 1:   # 두 기술을 함께 다루거나 기술명이 없는 문장은 그대로
            return sent

        def keep(g):
            ok = []
            for n, p in CITE_ITEM.findall(g[1]):
                i = int(n) - 1
                owner = techs_of.get(cites.order[i], set()) if 0 <= i < len(cites.order) else set()
                if not owner or named[0] in owner:
                    ok.append(f"[{n}, p.{p}]" if p else f"[{n}]")
            lead = g[0][: len(g[0]) - len(g[0].lstrip())]
            return lead + " ".join(ok) if ok else ""

        return CITE_GROUP.sub(keep, sent)

    return "\n".join(SENTENCE.sub(fix_sentence, line) for line in text.split("\n"))


DATE_PATTERNS = [
    re.compile(r'"(?:datePublished|uploadDate|dateCreated)"\s*:\s*"(\d{4}-\d{2}-\d{2})'),
    re.compile(r'<meta[^>]+name=["\']citation_(?:publication_|online_)?date["\'][^>]*content=["\'](\d{4}[/-]\d{2}[/-]\d{2})', re.I),
    re.compile(r'<meta[^>]+(?:property|name|itemprop)=["\'](?:article:published_time|og:published_time|datePublished|'
               r'pubdate|publishdate|publish-date|date|dc\.date|parsely-pub-date|sailthru\.date)["\'][^>]*?content=["\']'
               r'(\d{4}-\d{2}-\d{2})', re.I),
    re.compile(r'<meta[^>]+content=["\'](\d{4}-\d{2}-\d{2})[^"\']*["\'][^>]*(?:property|name|itemprop)=["\']'
               r'(?:article:published_time|og:published_time|datePublished|pubdate|date)["\']', re.I),
    re.compile(r'<time[^>]+datetime=["\'](\d{4}-\d{2}-\d{2})', re.I),
]


URL_DATE = re.compile(r"/(20\d{2})[/-](\d{2})[/-](\d{2})(?:/|$)")


def _published_date(url: str) -> str:
    """웹 페이지에 적힌 게시일(메타데이터·URL)을 읽는다. 못 찾거나 실패하면 빈 문자열."""
    m = URL_DATE.search(url)
    if m:
        return "-".join(m.groups())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            html = resp.read(400_000).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001 — 게시일은 부가 정보라 실패해도 보고서는 계속
        return ""
    for pat in DATE_PATTERNS:
        m = pat.search(html)
        if m:
            return m[1].replace("/", "-")
    return ""


def fill_missing_dates(references: list, fetch=_published_date) -> list:
    """게시일이 비어 있는 웹 출처만 페이지에서 게시일을 채운다 (검색 도구가 게시일을 주지 않는 경우 대비).
    ponytail: 순차 대신 10개 병렬, 페이지당 6초 제한. 느리면 워커 수를 늘린다."""
    targets = sorted({r["url"] for r in references if r.get("kind") == "web" and not r.get("date") and r.get("url")})
    if not targets:
        return references
    with ThreadPoolExecutor(max_workers=10) as pool:
        found = dict(zip(targets, pool.map(fetch, targets)))
    return [{**r, "date": found.get(r.get("url"), "")} if r.get("kind") == "web" and not r.get("date") else r
            for r in references]
