"""공유 State 정의 — 모든 노드가 이 파일의 타입만 import해서 쓴다.

기준: 설계서 docs/RAG-Design_v5.md  D-1 State 설계
변경은 5번(그래프 총괄)만 한다. 필드를 추가·변경해야 하면 직접 고치지 말고 요청할 것.

규칙
- 노드 함수는 `def xxx_node(state: State) -> dict` 형태이고, 자기 담당 키만 담은 dict를 반환한다.
- 관점 결과(tech_summary, trl_result, market_result, stakeholder_result, domain_result)는
  항상 두 기술("TurboQuant", "InfiniGen") 키를 모두 채워 반환한다. 근거가 없으면 빈 리스트.
- 출처는 리스트 인덱스가 아니라 source_id로 참조한다.
  웹: "web:<sha1(정규화 URL)[:10]>"   논문 청크: "arxiv:<id>#p<page>"
- references는 reducer(operator.add)로 누적되므로, 노드는 "이번에 새로 쓴 출처"만 반환한다.
"""

import operator
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
# 그래프 State
# ---------------------------------------------------------------------------
class State(TypedDict, total=False):
    # 입력
    tech_sw: TechName
    tech_hw: TechName
    domain: str
    # 1. 기술 조사
    tech_summary: dict[TechName, TechSummary]
    # 2~4. 평가 (Fan-out, 키 분리)
    trl_result: dict[TechName, TRLEstimate]        # 시장 평가 노드가 market_result와 함께 반환
    market_result: dict[TechName, MarketResult]
    stakeholder_result: dict[TechName, StakeholderResult]
    domain_result: dict[TechName, DomainResult]
    # 5. 충분성 검사 / 루프 제어
    sufficiency: SufficiencyCheck
    retry_count: int
    # 6~7. 종합·보고서
    synthesis: Synthesis
    references: Annotated[list[Reference], operator.add]
    report_path: str


def make_initial_state() -> State:
    """app.py가 graph.invoke()에 넘기는 초기 State."""
    return {
        "tech_sw": TECH_SW,
        "tech_hw": TECH_HW,
        "domain": DOMAIN,
        "retry_count": 0,
        "references": [],
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
