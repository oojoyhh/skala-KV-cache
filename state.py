"""공유 State 정의 — 모든 노드가 이 파일의 타입만 import해서 쓴다.

기준: docs/AGENT_CONTRACT.md 2장 (Supervisor 계약 v2.2.3)
      기존 타입은 설계서 docs/RAG-Design_v5.md  D-1 State 설계를 그대로 따른다.
변경은 5번(그래프 총괄)만 한다. 필드를 추가·변경해야 하면 직접 고치지 말고 요청할 것.

규칙
- 노드 함수는 `def xxx_node(state: State) -> dict` 형태이고, 자기 담당 키만 담은 dict를 반환한다.
- 관점 결과(tech_summary, trl_result, market_result, stakeholder_result, domain_result)는
  항상 두 기술("TurboQuant", "InfiniGen") 키를 모두 채워 반환한다. 근거가 없으면 빈 리스트.
- 출처는 리스트 인덱스가 아니라 source_id로 참조한다.
  웹: "web:<sha1(정규화 URL)[:10]>"   논문 청크: "arxiv:<id>#p<page>"
- references는 reducer(operator.add)로 누적되므로, 노드는 "이번에 새로 쓴 출처"만 반환한다.
- 모든 하위 노드는 성공이든 실패든 node_result를 반드시 반환한다.
  node_result.dispatch_id에는 state["control"]["dispatch_id"]를 그대로 복사한다.
- control은 supervisor만 수정한다. 하위 노드는 control을 읽기만 하고 반환하지 않는다.
"""

import operator
import uuid
from typing import Annotated, Literal, TypedDict

# ---------------------------------------------------------------------------
# 기본 타입
# ---------------------------------------------------------------------------
TechName = Literal["TurboQuant", "InfiniGen"]
Stance = Literal["positive", "negative", "neutral"]
# stance 정의 (설계서 v5 D-1): positive = 지지(supporting), negative = 한계·반론(limitation/challenging),
# neutral = 중립·배경. negative는 "나쁜 평가"가 아니며, 검색으로 확보된 근거만 쓴다(개수 맞추기용 생성 금지).

TECH_SW: TechName = "TurboQuant"
TECH_HW: TechName = "InfiniGen"
TECHS: tuple[TechName, ...] = (TECH_SW, TECH_HW)

# 충분성 검사가 판정하는 4개 관점 (SufficiencyCheck 키, reasons 키와 동일)
Perspective = Literal["trl", "market", "stakeholder", "domain"]
PERSPECTIVES: tuple[Perspective, ...] = ("trl", "market", "stakeholder", "domain")

DOMAIN = "데이터센터/클라우드 서빙"


# ---------------------------------------------------------------------------
# 출처·근거
# ---------------------------------------------------------------------------
class Reference(TypedDict):
    source_id: str       # 웹 "web:<sha1(url)[:10]>", 논문 "arxiv:<id>#p<page>" — 병렬 병합에도 불변
    kind: Literal["paper", "patent", "web"]
    author: str          # 저자 / 출원인 / 기관명
    date: str            # 논문·특허 "YYYY"/"YYYY-MM", 웹 "YYYY-MM-DD"
    title: str
    venue: str           # 학회지·권(호)·페이지 / 특허번호 / 사이트명
    url: str
    used_by: list[str]   # 사용한 에이전트들 (보고서 생성 시 source_id로 병합)
    stance: Stance       # 확증편향 점검용


class Evidence(TypedDict):
    claim: str
    source_id: str       # Reference.source_id 참조 (리스트 인덱스 사용 금지)
    stance: Stance


# ---------------------------------------------------------------------------
# 에이전트별 결과
# ---------------------------------------------------------------------------
class TechSummary(TypedDict):          # 1. 기술 조사 (RAG)
    name: TechName
    camp: Literal["SW", "HW"]
    approach: str
    scope: str
    key_metrics: dict
    limitations: list[str]
    evidence: list[Evidence]


class TRLEstimate(TypedDict):          # 2-a. 기술 성숙도 (시장 평가 에이전트가 산출)
    level: int           # 1~9
    rationale: str
    evidence: list[Evidence]
    uncertainty: str     # "공개 정보 기반 추정" 명시


class MarketResult(TypedDict):         # 2-b. 시장성
    market_size_growth: list[Evidence]
    adoption: list[Evidence]           # 상용화·채택 현황
    ecosystem: list[Evidence]          # 프레임워크 지원·표준화
    summary: str


class StakeholderResult(TypedDict):    # 3. 이해관계자 평가
    competitors: list[Evidence]
    adopters_devs: list[Evidence]
    investors: list[Evidence]
    summary: str


class DomainResult(TypedDict):         # 4. 도메인 평가 (데이터센터/클라우드 서빙)
    domain: str
    cost: list[Evidence]
    throughput: list[Evidence]
    model_quality: list[Evidence]      # 압축·오프로딩 후 품질 유지 여부 (TurboQuant 핵심)
    transfer_overhead: list[Evidence]  # GPU↔호스트 전송 부담 (InfiniGen 핵심)
    deployment_barrier: list[Evidence]
    summary: str


class SufficiencyCheck(TypedDict):     # 5. 충분성 검사 — 4개 관점 각각 판정
    trl: bool
    market: bool
    stakeholder: bool
    domain: bool
    reasons: dict[str, str]  # 부족 관점 → 사유(재조사 쿼리 힌트). 키는 PERSPECTIVES 중 하나


class Conflict(TypedDict):
    perspective_a: str
    perspective_b: str
    tech: TechName
    description: str


class Synthesis(TypedDict):            # 6. 평가 종합
    agreements: list[str]
    conflicts: list[Conflict]
    neutrality_note: str
    limitations: list[str]


# ---------------------------------------------------------------------------
# Supervisor 제어 (계약 v2.2.3 2장)
# ---------------------------------------------------------------------------
# supervisor가 고르는 하위 노드 7개
NodeName = Literal["research", "market", "stakeholder", "domain", "synthesis", "report", "quality"]
NODES: tuple[NodeName, ...] = ("research", "market", "stakeholder", "domain", "synthesis", "report", "quality")
# 근거 부족 재조사 대상 노드 (evidence_retry_counts 키)
EVIDENCE_RETRY_NODES: tuple[NodeName, ...] = ("market", "stakeholder", "domain")
# 관점 → 그 관점 근거를 만드는 노드 (TRL과 시장성은 같은 market 노드가 만든다)
PERSPECTIVE_NODE: dict[Perspective, NodeName] = {"trl": "market", "market": "market", "stakeholder": "stakeholder", "domain": "domain"}


class NodeResult(TypedDict):             # 하위 노드 → supervisor 실행 결과 보고
    node: NodeName
    dispatch_id: int                     # state["control"]["dispatch_id"]를 그대로 돌려준다
    status: Literal["success", "failed"]
    error: str                           # 실패 시 "E-xxxx 사유", 성공 시 ""


class MetricResult(TypedDict):           # 품질 항목 하나의 판정
    rule_passed: bool
    judge_passed: bool | None            # Judge를 쓰지 않거나 판정 불가면 None
    passed: bool                         # rule_passed and judge_passed is not False
    reasons: list[str]                   # 미달 사유(한국어, 문제 문장·절 포함)


class QualityResult(TypedDict):          # quality 노드 산출물
    groundedness: MetricResult
    neutrality: MetricResult
    bias_control: MetricResult
    perspective_coverage: MetricResult
    page_count: int
    page_limit_passed: bool              # page_count <= MAX_REPORT_PAGES
    required_sections_passed: bool       # SUMMARY·REFERENCE 절 존재
    passed: bool                         # 네 항목 passed and page_limit_passed and required_sections_passed
    feedback: list[str]                  # report 재작성 입력
    evaluated_report_version: int        # 평가한 report_version
    action: Literal["pass", "rewrite", "research"]  # 품질 노드의 권고 (다음 노드는 supervisor가 정함)
    target_node: str                     # action == "research"일 때 market | stakeholder | domain, 그 외 ""


class ControlState(TypedDict):           # supervisor만 수정
    next_node: str                       # "" (최초) | 노드 이름 | "END"
    route_reason: str
    dispatch_id: int                     # 노드 호출마다 step_count 값으로 갱신
    step_count: int                      # 하위 노드 실행 횟수
    exec_retry_counts: dict[str, int]    # 실행 실패 재시도. 7개 노드 모두 0으로 시작
    evidence_retry_counts: dict[str, int]  # 근거 부족 재조사. market·stakeholder·domain
    report_retry_count: int              # 품질 미달 재작성
    node_status: dict[str, str]          # "success" | "failed" | "skipped"
    node_errors: dict[str, str]          # 노드별 마지막 오류
    last_error: str
    finalize_tried: list[str]            # 마무리 모드에서 시도한 노드
    quality_research_count: int          # 품질 평가 기반 재조사 횟수 (실행 전체)
    stale: list[str]                     # 산출물이 있어도 다시 만들어야 하는 노드 (synthesis·report)
    trace_id: str                        # LangSmith metadata.trace_id와 같은 값
    run_status: Literal["running", "completed", "exhausted"]


# ---------------------------------------------------------------------------
# 그래프 State
# ---------------------------------------------------------------------------
class State(TypedDict, total=False):
    # ── 1. 입력 ───────────────────────────────────────────
    tech_sw: TechName
    tech_hw: TechName
    domain: str
    # ── 2. Agent 페이로드 (작업 결과) ─────────────────────────
    tech_summary: dict[TechName, TechSummary]      # research
    trl_result: dict[TechName, TRLEstimate]        # market (market_result와 함께 반환)
    market_result: dict[TechName, MarketResult]    # market
    stakeholder_result: dict[TechName, StakeholderResult]  # stakeholder
    domain_result: dict[TechName, DomainResult]    # domain
    sufficiency: SufficiencyCheck                  # supervisor가 evaluate_sufficiency() 결과를 저장
    synthesis: Synthesis                           # synthesis
    references: Annotated[list[Reference], operator.add]  # 모든 조사 노드 (reducer로 누적)
    report_path: str                     # report — 기존 이름 유지 (PDF)
    report_md_path: str                  # report — 품질 평가 원본 (보고서 전문은 State에 넣지 않음)
    report_version: int                  # report — 실행마다 +1 (0 = 보고서 없음)
    # ── 3. 노드 실행 결과 ─────────────────────────────────
    node_result: NodeResult              # 모든 하위 노드 → supervisor
    # ── 4. 평가 ───────────────────────────────────────────
    quality_result: QualityResult        # quality
    # ── 5. 제어 (supervisor 전용) ───────────────────────────
    control: ControlState


def make_initial_state() -> State:
    """app.py가 graph.invoke()에 넘기는 초기 State."""
    return {
        "tech_sw": TECH_SW,
        "tech_hw": TECH_HW,
        "domain": DOMAIN,
        "references": [],
        "report_version": 0,
        "control": {
            "next_node": "",
            "route_reason": "",
            "dispatch_id": 0,
            "step_count": 0,
            "exec_retry_counts": {n: 0 for n in NODES},
            "evidence_retry_counts": {n: 0 for n in EVIDENCE_RETRY_NODES},
            "report_retry_count": 0,
            "node_status": {},
            "node_errors": {},
            "last_error": "",
            "finalize_tried": [],
            "quality_research_count": 0,
            "stale": [],
            "trace_id": uuid.uuid4().hex,
            "run_status": "running",
        },
    }


# 관점 → (State 키, Evidence가 들어 있는 필드들). 충분성 검사·평가 종합·보고서(요약 매트릭스)가 공통으로 사용
PERSPECTIVE_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "trl": ("trl_result", ("evidence",)),
    "market": ("market_result", ("market_size_growth", "adoption", "ecosystem")),
    "stakeholder": ("stakeholder_result", ("competitors", "adopters_devs", "investors")),
    "domain": ("domain_result", ("cost", "throughput", "model_quality", "transfer_overhead", "deployment_barrier")),
}


def perspective_evidence(state: State, perspective: str, tech: TechName) -> list[Evidence]:
    """한 관점·한 기술의 Evidence를 모두 모아 반환한다. 결과가 없으면 빈 리스트.

    같은 `(source_id, claim)`이 한 관점의 여러 필드에 들어갈 수 있으므로(예: 같은 근거를
    이해관계자 두 그룹에 배치) 여기서 한 번만 세도록 중복을 제거한다. 충분성 검사·평가 종합·
    보고서가 모두 이 함수를 쓰므로 근거 개수가 세 곳에서 같아진다 (DEV_PLAN §3-3).
    """
    key, fields = PERSPECTIVE_FIELDS[perspective]
    result = state.get(key, {}).get(tech, {})
    unique: dict[tuple[str, str], Evidence] = {}
    for f in fields:
        for ev in result.get(f, []):
            unique.setdefault((ev["source_id"], ev["claim"]), ev)
    return list(unique.values())


def retry_hint(state: State, perspective: str) -> str:
    """재조사 시 충분성 검사가 남긴 부족 사유를 꺼낸다. 첫 실행이면 빈 문자열.

    평가 노드는 이 값이 있으면 검색 쿼리를 바꿔야 한다(같은 쿼리 반복 금지).
    """
    return state.get("sufficiency", {}).get("reasons", {}).get(perspective, "")
