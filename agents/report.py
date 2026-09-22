"""📝 보고서 생성 에이전트 (6번) — State → 설계서 E 목차 순서의 평가 보고서 PDF.

- 챕터마다 필요한 State만 LLM에 넘겨 본문을 쓰고, SUMMARY는 본문을 다 쓴 뒤 작성해 맨 앞에 둔다.
- 인용 번호는 코드가 매긴다: references를 문서 단위 source_id(#p 제거)로 중복 제거·used_by 병합 →
  본문 등장 순서로 번호, 논문 청크는 [n, p.쪽]. REFERENCE에는 State의 Evidence가 가리키는 출처만 싣는다
  (설계서 D-1 "실제 사용한 자료만". 검색만 되고 근거로 쓰이지 않은 결과는 제외). 본문 인용 순서대로, 인용되지 않은 출처는 그 뒤에.
- SUMMARY는 소개(인트로덕션)가 아니라 결론 요약이다: 배경·기술 선정 챕터는 빼고 평가 결과만 넘긴다.
- 표(요약 매트릭스, 4-4 도메인 5개 지표)와 REFERENCE는 LLM 없이 코드로 만든다.
- LLM 호출이 실패해도 멈추지 않는다: 해당 챕터는 근거 목록으로 대신 싣고 "[E-1002]"를 남긴다.
"""

import glob
import json
import os
import re
import unicodedata
from email.utils import parsedate_to_datetime

from fontTools.ttLib import TTFont
from fpdf import FPDF
from fpdf.fonts import FontFace

import config
import llm
from state import PERSPECTIVES, TECHS, State, perspective_evidence

TITLE = "KV cache 최적화 기술 다관점 평가 보고서"
TRL_NOTE = ("본 TRL은 공개 정보 기반 추정이며, KV cache 기술은 논문 발표 시점과 실제 채택 간 시차가 있어 "
            "실제 단계와 다를 수 있다.")
PERSPECTIVE_NAMES = {"trl": "TRL", "market": "시장성", "stakeholder": "이해관계자", "domain": "도메인"}
DOMAIN_METRICS = {"cost": "비용", "throughput": "처리량", "model_quality": "모델 품질",
                  "transfer_overhead": "전송 오버헤드", "deployment_barrier": "도입 난이도"}


# ---------------------------------------------------------------------------
# 인용 · REFERENCE
# ---------------------------------------------------------------------------
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
        for r in references:
            d = _doc_id(r["source_id"])
            if d not in used_docs:
                continue
            if d in self.docs:
                merged = self.docs[d]["used_by"]
                merged += [u for u in r.get("used_by", []) if u not in merged]
            else:
                self.docs[d] = {**r, "used_by": list(r.get("used_by", []))}
        self.order: list[str] = []

    def cite(self, source_id: str) -> str | None:
        d = _doc_id(source_id)
        if d not in self.docs:
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


def format_reference(r) -> str:
    """노션 가이드 REFERENCE 표기 형식. 작성자가 없으면 기관(사이트)명을 쓴다."""
    v, t, u = r["venue"], r["title"], r.get("url", "")
    a = (r.get("author") or "").strip() or v
    d = _date(r.get("date", ""), r["kind"])
    if r["kind"] == "patent":
        return f"{a}({d}). {t}, {v}, {u}"
    if r["kind"] == "paper":
        return f"{a}({d}). {t}. {v}."
    return f"{a}({d}). {t}. {v}, {u}"


def _drop_bad_citations(text: str, n_refs: int) -> str:
    """REFERENCE에 없는 번호의 인용([99] 등)을 본문에서 지운다 (인용·참고문헌 연결 검사)."""
    return re.sub(r"\[(\d+)(, p\.\d+)?\]", lambda m: m[0] if 1 <= int(m[1]) <= n_refs else "", text)


# ---------------------------------------------------------------------------
# 표 (LLM 없이 State에서 계산)
# ---------------------------------------------------------------------------
STANCE_NOTE = "표의 숫자: 지지 / 한계·반론 / 중립 근거 수"


def _stance_counts(evs) -> str:
    if not evs:
        return "근거 없음"
    return " / ".join(str(sum(e["stance"] == s for e in evs)) for s in ("positive", "negative", "neutral"))


def summary_matrix(state: State) -> list[list[str]]:
    """기술 × 관점 근거 분포 (DEV_PLAN §8 #10: perspective_evidence의 stance 개수)."""
    rows = [["기술", *(PERSPECTIVE_NAMES[p] for p in PERSPECTIVES)]]
    for tech in TECHS:
        rows.append([tech, *(_stance_counts(perspective_evidence(state, p, tech)) for p in PERSPECTIVES)])
    return rows


def domain_table(state: State, cites: Citations) -> list[list[str]]:
    """4-4 도메인 5개 지표 표. 근거가 없는 지표는 "해당 없음"."""
    rows = [["지표", *TECHS]]
    for key, name in DOMAIN_METRICS.items():
        row = [name]
        for tech in TECHS:
            evs = state.get("domain_result", {}).get(tech, {}).get(key, [])
            marks = list(dict.fromkeys(c for c in (cites.cite(e["source_id"]) for e in evs) if c))
            row.append(f"{_stance_counts(evs)} {' '.join(marks)}" if evs else "해당 없음")
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 본문 (설계서 E 목차 순서)
# ---------------------------------------------------------------------------
def _by_tech(state, key, drop=()):
    return {t: {k: v for k, v in state.get(key, {}).get(t, {}).items() if k not in drop} for t in TECHS}


# (제목 수준, 제목, 분량, 지시, State → 입력). pick이 None이면 제목만 쓴다.
CHAPTERS = [
    (2, "1. 분석 배경", "400~500자",
     "KV cache가 왜 메모리 병목이 되는지, 선정 도메인(데이터센터/클라우드 서빙)에서 왜 분석해야 하는지 서술",
     lambda s: {"domain": s.get("domain"), "tech_summary": _by_tech(s, "tech_summary")}),
    (2, "2. 기술 선정", "400~500자",
     "SW 진영(데이터를 작게)과 HW 진영(담을 공간을 넓게)에서 1건씩 고른 이유와, 같은 병목을 반대 방향에서 푼다는 대비를 서술",
     lambda s: {"tech_sw": s.get("tech_sw"), "tech_hw": s.get("tech_hw"),
                "tech_summary": _by_tech(s, "tech_summary", drop=("evidence",))}),
    (2, "3. 기술 개요", "1,600~2,000자",
     "기술별로 접근 방식, 적용 범위, 핵심 수치(비교 기준 포함), 한계를 나눠 서술",
     lambda s: {"tech_summary": _by_tech(s, "tech_summary")}),
    (2, "4. 관점별 평가", None, None, None),
    (3, "4-1. 기술 성숙도 (TRL)", "800~1,000자",
     "기술별 추정 TRL 단계와 판정 근거, 더 높은 단계로 보기 어려운 이유를 서술",
     lambda s: {"trl_result": _by_tech(s, "trl_result")}),
    (3, "4-2. 시장성", "800~1,000자",
     "시장 규모·성장성, 상용화·채택 현황, 생태계 지지를 구분해 서술. 관련 시장의 성장과 해당 기술의 채택을 구분",
     lambda s: {"market_result": _by_tech(s, "market_result")}),
    (3, "4-3. 이해관계자", "800~1,000자",
     "경쟁 기술 진영, 도입 기업·개발자, 투자 업계별 반응을 지지·한계·반론 모두 서술",
     lambda s: {"stakeholder_result": _by_tech(s, "stakeholder_result")}),
    (3, "4-4. 도메인 적용", "600~800자",
     "위 표의 5개 지표(비용, 처리량, 모델 품질, 전송 오버헤드, 도입 난이도)를 기준으로 데이터센터/클라우드 서빙에서의 평가를 서술. "
     "근거가 없는 지표는 해당 없음으로 둔다",
     lambda s: {"domain_result": _by_tech(s, "domain_result")}),
    (2, "5. 시사점", "900~1,000자",
     "관점에 따라 평가가 엇갈리는 지점(conflicts)을 중심으로, 관점 간 일치 지점(agreements)과 함께 서술",
     lambda s: {k: s.get("synthesis", {}).get(k, []) for k in ("agreements", "conflicts")}),
    (2, "6. 한계점", "400~500자",
     "공개 정보 기반 추정의 한계, 확증편향을 막기 위해 취한 조치(지지·한계·반론 쿼리 병행, 충분성 검사와 재조사), "
     "재조사 후에도 근거가 부족했던 관점을 서술",
     lambda s: {"limitations": s.get("synthesis", {}).get("limitations", []),
                "neutrality_note": s.get("synthesis", {}).get("neutrality_note", ""),
                "sufficiency_reasons": s.get("sufficiency", {}).get("reasons", {}),
                "retry_count": s.get("retry_count", 0)}),
]


def _synthesis_for_report(data: dict, cites: Citations, allowed_ids: set[str]) -> dict:
    """State의 문자열 계약을 유지하며 종합 문장 끝의 출처를 cite로 변환한다."""
    def cited_claim(text: str) -> dict | None:
        match = re.fullmatch(r"(.*) \(출처: ([^)]+)\)", text, flags=re.DOTALL)
        if not match:
            return None
        claim, raw_ids = match.groups()
        ids = list(dict.fromkeys(s.strip() for s in raw_ids.split(",")))
        # 일부 출처만 존재해도 문장 전체를 승인하지 않는다.
        if not claim.strip() or any(s not in allowed_ids or _doc_id(s) not in cites.docs for s in ids):
            return None
        marks = list(dict.fromkeys(cites.cite(s) for s in ids))
        return {"claim": claim, "cite": " ".join(marks)}

    agreements = []
    conflicts = []
    for text in data.get("agreements", []):
        item = cited_claim(text)
        if item:
            agreements.append(item)
    for conflict in data.get("conflicts", []):
        item = cited_claim(conflict["description"])
        if item:
            conflicts.append({k: conflict[k] for k in ("tech", "perspective_a", "perspective_b")} | item)
    return {"agreements": agreements, "conflicts": conflicts}


def _claims(data) -> list[str]:
    """LLM 실패 시 대체 본문: 입력에 있는 근거를 인용과 함께 나열."""
    if isinstance(data, list):
        return [c for v in data for c in _claims(v)]
    if isinstance(data, dict):
        if "claim" in data:
            return [f"- {data['claim']} {data['cite']}"] if data.get("cite") else []
        return [c for v in data.values() for c in _claims(v)]
    return []


def _write(generate, prompt_head, title, length, instruction, data) -> str:
    prompt = (f"{prompt_head}\n\n## 챕터: {title}\n분량: {length}\n지시: {instruction}\n"
              f"입력:\n{json.dumps(data, ensure_ascii=False, indent=1)}")
    try:
        return generate(prompt).strip()
    except llm.LLMError as exc:
        fallback = "\n".join(_claims(data)) or "공개 근거 미확인"
        return f"[E-1002] 문장 생성 실패로 근거 목록을 그대로 싣는다. ({exc})\n{fallback}"


def build_blocks(state: State, generate) -> list[tuple]:
    """보고서를 (종류, 내용) 블록 목록으로 만든다. 종류: h1·h2·h3·p·table."""
    head = llm.load_prompt("report")
    cites = Citations(state.get("references", []), evidence_source_ids(state))
    body = []
    for level, title, length, instruction, pick in CHAPTERS:
        body.append((f"h{level}", title))
        if pick is None:
            continue
        if title.startswith("4-4"):
            body += [("note", STANCE_NOTE), ("table", domain_table(state, cites))]
        data = pick(state)
        if title == "5. 시사점":
            data = _synthesis_for_report(data, cites, evidence_source_ids(state))
        else:
            data = cites.attach(data)
        text = _write(generate, head, title, length, instruction, data)
        if title.startswith("4-1") and "공개 정보 기반 추정" not in text:
            text += "\n" + TRL_NOTE
        if title.startswith("6.") and state.get("sufficiency", {}).get("reasons"):
            # 충분성 미달 사유는 LLM이 빠뜨려도 남도록 코드로 붙인다
            text += "\n\n충분성 검사 미달 사유 (재조사 상한 도달 시 근거를 만들지 않고 기록):\n" + "\n".join(
                f"- {PERSPECTIVE_NAMES.get(p, p)}: {why}" for p, why in state["sufficiency"]["reasons"].items())
        body.append(("p", text))

    # SUMMARY 입력은 평가 결과 챕터(3장~6장)만. 배경·기술 선정을 넘기면 소개문이 되기 쉽다
    start = body.index(("h2", "3. 기술 개요"))
    findings = "\n\n".join(t for kind, t in body[start:] if kind in ("h2", "h3", "p"))
    summary = _write(generate, head, "SUMMARY", "400~600자 (반 페이지 이내)",
                     "보고서 전체의 결론 요약을 쓴다. 인트로덕션이 아니다. '본 보고서는', 배경·목적·기술 소개 문장으로 시작하지 않고 "
                     "첫 문장부터 평가 결과를 쓴다. 관점별 핵심 평가(TRL·시장성·이해관계자·도메인)와 관점 간 평가가 엇갈리는 지점을 "
                     "\"- \"로 시작하는 4~6개 항목으로 쓰고, 본문의 인용 표기를 유지",
                     {"평가 결과": findings})

    refs = cites.reference_lines()
    blocks = [("h1", TITLE), ("h2", "SUMMARY"), ("p", summary), ("note", STANCE_NOTE), ("table", summary_matrix(state)),
              *body, ("h2", "REFERENCE"), ("p", "\n".join(refs) or "인용된 자료 없음")]
    return [(k, _drop_bad_citations(v, len(refs)) if k == "p" else v) for k, v in blocks]


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------
def _font_file() -> str:
    found = sorted(glob.glob(os.path.join(config.FONT_DIR, "*.ttf")))
    if not found:
        raise FileNotFoundError(f"한글 폰트가 없습니다. {config.FONT_DIR}/ 에 .ttf 파일을 두세요.")
    return found[0]


def _printable(text: str, cmap: dict) -> str:
    """폰트에 없는 글자가 PDF에서 조용히 사라지지 않게 바꾼다: α → alpha, ① → (1), 그 밖(이모지 등)은 제거."""
    out = []
    for ch in text:
        if ord(ch) in cmap or ch == "\n":
            out.append(ch)
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("GREEK"):
            out.append(name.split()[-1].lower().replace("lamda", "lambda"))  # 유니코드 이름 표기가 LAMDA
        elif name.startswith(("CIRCLED DIGIT", "CIRCLED NUMBER")):
            out.append(f"({unicodedata.numeric(ch):g})")
    return "".join(out)


def render_pdf(blocks, path: str) -> None:
    font = _font_file()
    cmap = TTFont(font).getBestCmap()
    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_font("ko", fname=font)
    pdf.add_page()
    sizes = {"h1": 18, "h2": 14, "h3": 12}
    for kind, content in blocks:
        content = [[_printable(c, cmap) for c in row] for row in content] if kind == "table" else _printable(content, cmap)
        if kind in sizes:
            pdf.ln(4)
            pdf.set_font("ko", size=sizes[kind])
            pdf.multi_cell(0, 8, content, align="L", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif kind == "note":
            pdf.set_font("ko", size=8)
            pdf.multi_cell(0, 5, content, align="L", new_x="LMARGIN", new_y="NEXT")
        elif kind == "table":
            pdf.set_font("ko", size=8)
            with pdf.table(text_align="LEFT", line_height=5, headings_style=FontFace(emphasis="")) as table:
                for row in content:
                    cells = table.row()
                    for cell in row:
                        cells.cell(cell)
            pdf.ln(2)
        else:
            pdf.set_font("ko", size=10)
            for line in content.replace("**", "").splitlines():
                line = line.lstrip("#").lstrip() if line.startswith("#") else line  # LLM이 붙인 마크다운 제목 기호 제거
                pdf.multi_cell(0, 6, line.rstrip(), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)


def to_markdown(blocks) -> str:
    """디버깅·README 인용용 Markdown 사본."""
    out = []
    for kind, content in blocks:
        if kind == "table":
            out += ["| " + " | ".join(content[0]) + " |", "|" + "---|" * len(content[0])]
            out += ["| " + " | ".join(r) + " |" for r in content[1:]]
        else:
            out.append({"h1": "# ", "h2": "## ", "h3": "### "}.get(kind, "") + content)
        out.append("")
    return "\n".join(out)


def report_node(state: State, generate_fn=None) -> dict:
    blocks = build_blocks(state, generate_fn or llm.generate)
    os.makedirs(os.path.dirname(config.REPORT_PATH), exist_ok=True)
    render_pdf(blocks, config.REPORT_PATH)
    with open(os.path.splitext(config.REPORT_PATH)[0] + ".md", "w", encoding="utf-8") as f:
        f.write(to_markdown(blocks))
    return {"report_path": config.REPORT_PATH}
