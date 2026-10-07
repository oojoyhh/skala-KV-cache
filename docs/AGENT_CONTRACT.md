# Agent 과제 공통 계약 (Supervisor 패턴) — v2.2

> **이 문서를 AI 도구에 작업 지시 맨 위에 붙여 넣는다.**
> "이 계약을 절대 변경하지 말고, 내 담당 파일만 수정하라."
> 계약을 바꿔야 하면 코드를 고치지 말고 공통 파일 담당자에게 변경을 제안한다.
> 결정 배경은 `docs/AGENT_DECISIONS.md`를 본다.

- 상태: **Freeze v2.2.1** (2026-10-07, 팀 피드백 fix1~fix4 + 가이드 재검토 + 품질 평가 기반 재조사 + 셀프 검토 반영)
- 기존 계약(`docs/DEV_PLAN.md`, `state.py` 타입)은 이 문서와 충돌하지 않는 한 그대로 유효하다.

---

## 1. 라우팅

```
START → select → supervisor
supervisor ─┬→ research    ─┐
            ├→ market       │
            ├→ stakeholder  │
            ├→ domain       ├→ supervisor   (모든 하위 노드는 실행 후 supervisor로 복귀)
            ├→ synthesis    │
            ├→ report       │
            └→ quality     ─┘
supervisor → END
```

1. 하위 노드끼리 직접 edge를 두지 않는다(`research → market`, `synthesis → report`, `report → quality` 금지).
2. `supervisor`만 `add_conditional_edges`로 분기한다. 한 번에 `next_node` **하나만** 고른다(순차 실행, 병렬 fan-out 없음).
3. 라우팅은 규칙 기반이다. 같은 State면 같은 경로가 나온다. 라우팅에 LLM을 쓰지 않는다.
4. 충분성 판정은 `supervisor` 안에서 `agents.check.evaluate_sufficiency(state)`를 호출해 한다. `check` 노드는 그래프에서 뺀다.
5. `supervisor`는 조정 계층이므로 하위 에이전트와 다른 폴더에 둔다: `orchestration/supervisor.py`.

### 1-1. 직전 실행 반영 (supervisor 진입 시 가장 먼저)

`control.next_node`가 `""`(최초 진입)이면 이 단계를 건너뛴다. 그 외에는 `node = control.next_node`에 대해:

1. `node_result`가 있고 `node_result.node == node`이고 `node_result.dispatch_id == control.dispatch_id`이면 그 결과를 쓴다.
2. 아니면 반환 누락으로 보고 `status = "failed"`, `error = "E-1002 node_result 누락: <node>"`로 처리한다.
3. `node_status[node] = status`. 실패면 `node_errors[node] = error`, `last_error = error`.
   성공이고 `node`가 `control.stale`에 있으면 `stale`에서 뺀다.
4. `step_count += 1` (`select`·`supervisor` 방문은 세지 않는다).

### 1-2. 다음 노드 선택 (위에서부터 처음 맞는 규칙)

**마무리 모드:** `step_count >= MAX_TOTAL_STEPS`이면 조사와 재시도를 멈추고 아래 순서만 본다. 각 노드는 마무리 모드에서 **한 번만** 시도한다(`control.finalize_tried`에 기록).

| 순위 | 조건 | `next_node` |
|---|---|---|
| F1 | 직전 노드가 `failed` | `END` |
| F2 | `synthesis` 없음 또는 `"synthesis" in stale`, `"synthesis"` 미시도 | `synthesis` |
| F3 | `report_version == 0` 또는 `"report" in stale`, `"report"` 미시도 | `report` |
| F4 | 보고서 있음, `quality_result.evaluated_report_version != report_version`, `"quality"` 미시도 | `quality` |
| F5 | 그 외 | `END` |

마무리 모드는 항상 `run_status = "exhausted"`로 끝난다. 마무리 모드의 추가 실행은 최대 3회라 종료가 보장된다.

**일반 모드:**

| 순위 | 조건 | `next_node` | 함께 바꾸는 `control` |
|---|---|---|---|
| 1 | 직전 노드가 `failed`이고 `exec_retry_counts[node] < MAX_EXEC_RETRY` | 같은 노드 | `exec_retry_counts[node] += 1` |
| 2 | 직전 노드가 `failed`이고 실행 재시도 상한 도달 | 조사 노드는 아래 규칙으로 계속(그 노드 제외). `synthesis`·`report`는 **산출물이 State에 있으면**(`synthesis` 존재, `report_version > 0`) 아래 규칙으로 계속하고 없으면 `END`. `quality`는 `END` | `node_status[node] = "skipped"`, `END`이면 `run_status = "exhausted"` |
| 3 | `tech_summary` 없음, `research`가 `skipped` 아님 | `research` | |
| 4 | 관점 노드 중 아직 실행 안 된(`node_status`에 없음) 것이 있음 | 후보 중 하나 (1-3 선택 방법) | |
| 5 | `evaluate_sufficiency()` 결과 부족 관점이 있고, 해당 노드가 `skipped` 아니며 `evidence_retry_counts < MAX_AGENT_RETRY` | 후보 중 하나 (1-3 선택 방법) | `evidence_retry_counts[node] += 1` |
| 6 | `synthesis` 없음 또는 `"synthesis" in stale` | `synthesis` | |
| 7 | `report_version == 0` 또는 `"report" in stale` | `report` | |
| 8 | `quality_result` 없음 또는 `evaluated_report_version != report_version` | `quality` | |
| 9a | `quality_result.passed == False`, `action == "research"`, `quality_research_count < MAX_QUALITY_RESEARCH`, `target_node`가 관점 노드이고 `skipped` 아님 | `target_node` (품질 평가 기반 재조사) | `quality_research_count += 1`, `stale = ["synthesis", "report"]`, `sufficiency`에 품질 사유 기록(1-4) |
| 9b | `quality_result.passed == False`이고 `report_retry_count < MAX_REPORT_RETRY` | `report` | `report_retry_count += 1` |
| 10 | 그 외 | `END` | 통과면 `run_status = "completed"`, 아니면 `"exhausted"` |

- `skipped`된 노드는 **어떤 규칙에서도** 다시 고르지 않는다.
- 규칙 9a로 재조사한 뒤에는 다시 규칙 4부터 본다. 충분성 재평가(규칙 5) → `synthesis` → `report` → `quality` 순으로 자연스럽게 다시 돈다.
- 9a 조건을 못 채우면(재조사 기회 소진·대상 skipped) 9b로 내려가 보고서 재작성으로 처리한다.
- 규칙 5를 평가할 때마다 `evaluate_sufficiency()` 결과를 `sufficiency`에 저장한다(재조사 노드의 `retry_hint`와 synthesis의 `limitations`가 읽는다).
- 관점 → 노드: `trl`·`market` → `market`, `stakeholder` → `stakeholder`, `domain` → `domain`. TRL과 시장성이 함께 부족해도 `market` 1회로 센다.
- 재조사 상한에 닿아 부족한 채 진행하면, 부족 사유는 synthesis가 `limitations`에 옮기고 `E-1005`로 명시한다(기존 동작 유지). 재조사 기회가 남았어도 마무리 모드(`run_status = "exhausted"`)로 멈췄으면 `"[E-1005] 실행 상한 도달로 재조사 중단: ..."`으로 명시한다.
- synthesis는 LLM 실패 시에도 대체 결과(한계 목록 + E-1002)를 반환하므로, 재시도까지 실패해도 규칙 2에 따라 report로 진행해 보고서를 남긴다.
- 노드를 고를 때마다 `control.dispatch_id = step_count`, `next_node`, `route_reason`을 쓴다. `END`이면 `next_node = "END"`.
- `route_reason`은 한국어 한 줄로 쓴다(예: `"stakeholder 근거 부족(negative 0건), 후보 중 근거 최소 → 재조사 1/2"`).
- 품질 분기(규칙 9a·9b·10)의 `route_reason`은 **"미달 항목 — 품질 평가 권고와 판단 → 선택"** 형식으로, 재조사를 고르지 않았으면 그 이유까지 남긴다. 미달 항목은 `MetricResult.passed`가 False인 항목과 첫 사유다.
  - 9a: `"품질 미달(perspective_coverage: InfiniGen 시장성 미확보) — 품질 평가 권고: market 추가 조사, 기회 0/1 → market 재조사 1/1"`
  - 9b: `"품질 미달(perspective_coverage: ...) — 품질 평가 권고: market 추가 조사, 기회 소진(1/1) → 보고서 재작성 1/1"`, `"품질 미달(neutrality: 우열 암시 표현) — 품질 평가 권고: 재작성 → 보고서 재작성 1/1"`
  - 10: `"품질 미달(...) — 품질 평가 권고: 재작성, 재작성 기회 소진(1/1) → 종료"` 과거 결정은 State에 쌓지 않는다. Supervisor 노드 출력이 LangSmith에 매번 기록되어 결정 로그가 된다: `trace_id`(metadata), 노드, 결정(`next_node`), 사유(`route_reason`), 시각(LangSmith 자동).

### 1-3. 관점 후보 중 선택 방법

1. 후보는 State로 만든다(규칙 4: 미실행, 규칙 5: 부족하고 재조사 여유 있음).
2. **근거가 가장 적은 관점부터** 고른다. 기준은 `state.perspective_evidence()`의 두 기술 합계 개수다. 관점 노드 하나에 관점이 둘이면(`market`: trl+market) 더 적은 쪽 값을 쓴다.
3. 개수가 같을 때만 `market → stakeholder → domain` 순으로 정한다. 이 순서는 워크플로 순서가 아니라 **같은 State에서 같은 결과를 내기 위한 동점 처리 규칙**이다.
4. 코드에서 후보 계산(`candidate_nodes(state)`)과 동점 처리(`tie_break(candidates)`)를 분리한다.

### 1-4. 품질 평가 기반 재조사의 힌트 전달

- 규칙 9a로 보낼 때 supervisor는 `sufficiency`에서 `target_node`가 맡은 관점(`market`이면 `trl`·`market`)을 `False`로 두고, `reasons[관점] = "품질 평가: <quality_result.feedback 요약>"`을 넣는다.
- 관점 노드는 기존과 같이 `retry_hint(state, "<관점>")`로 이 사유를 읽어 쿼리를 바꾼다. 관점 노드 코드는 바꾸지 않는다.
- 재조사 후 supervisor가 규칙 5에서 `evaluate_sufficiency()`로 다시 평가해 `sufficiency`를 덮어쓴다.

---

## 2. State

**기존 필드는 이름·타입을 그대로 둔다.** 아래 기존 필드는 `state.py`의 현재 정의를 그대로 쓴다(축약하지 않는다).
특히 `references`의 Reducer(`Annotated[list[Reference], operator.add]`)를 지우지 않는다. 지우면 노드마다 출처 목록이 덮어써진다.

```python
NodeName = Literal["research", "market", "stakeholder", "domain", "synthesis", "report", "quality"]


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


class State(TypedDict, total=False):
    # 1. 입력 (기존)
    tech_sw: TechName
    tech_hw: TechName
    domain: str
    # 2. Agent 페이로드 (기존, 타입 그대로)
    tech_summary: dict[TechName, TechSummary]
    trl_result: dict[TechName, TRLEstimate]
    market_result: dict[TechName, MarketResult]
    stakeholder_result: dict[TechName, StakeholderResult]
    domain_result: dict[TechName, DomainResult]
    sufficiency: SufficiencyCheck
    synthesis: Synthesis
    references: Annotated[list[Reference], operator.add]
    report_path: str                     # 기존 이름 유지 (PDF)
    report_md_path: str                  # 신규: 품질 평가 원본 (보고서 전문은 State에 넣지 않음)
    report_version: int                  # 신규: report 실행마다 +1 (0 = 보고서 없음)
    # 3. 노드 실행 결과 (신규)
    node_result: NodeResult
    # 4. 평가 (신규)
    quality_result: QualityResult
    # 5. 제어 (신규)
    control: ControlState
```

- 삭제: `retry_count`. 새 코드에서 읽거나 쓰지 않는다.
- `make_initial_state()` 초기값: `report_version = 0`, `control = {next_node: "", route_reason: "", dispatch_id: 0, step_count: 0, exec_retry_counts: {7개 노드: 0}, evidence_retry_counts: {market·stakeholder·domain: 0}, report_retry_count: 0, node_status: {}, node_errors: {}, last_error: "", finalize_tried: [], quality_research_count: 0, stale: [], trace_id: uuid.uuid4().hex, run_status: "running"}`.

---

## 3. 읽기·쓰기 권한

| 노드 | 반환해도 되는 키 | 추가로 읽는 값 |
|---|---|---|
| `select` | `tech_sw`, `tech_hw`, `domain` | |
| `supervisor` | `control`, `sufficiency` | |
| `research` | `tech_summary`, `references`, `node_result` | `control.dispatch_id` |
| `market` | `trl_result`, `market_result`, `references`, `node_result` | `control.dispatch_id`, `retry_hint` |
| `stakeholder` | `stakeholder_result`, `references`, `node_result` | `control.dispatch_id`, `retry_hint` |
| `domain` | `domain_result`, `references`, `node_result` | `control.dispatch_id`, `retry_hint` |
| `synthesis` | `synthesis`, `node_result` | `control.dispatch_id` |
| `report` | `report_path`, `report_md_path`, `report_version`, `node_result` | `control.dispatch_id`, `quality_result.feedback`(재작성 시), `control.node_errors`(수집 오류 절) |
| `quality` | `quality_result`, `node_result` | `control.dispatch_id`, `report_md_path`, `report_path`, `report_version`, `references` |

1. 하위 노드는 `control`을 읽기만 하고 반환하지 않는다. 다음 노드를 정하지 않는다(`quality`도 마찬가지).
2. 모든 하위 노드는 **성공이든 실패든 `node_result`를 반드시 반환**한다. 예외를 밖으로 던지지 않는다.
3. 실패 시에는 `node_result`만 반환해도 된다. 기존 결과는 State에 그대로 남는다.

### 3-1. `failed` 기준과 에러 코드

- `failed`: 실행 자체가 안 된 경우만 해당한다. 예외, API 장애·키 없음(`E-1002`), 논문 PDF 로딩 실패(`E-1003`), Judge LLM 호출 실패(`E-1002`).
- `success`: 실행은 됐지만 결과가 적은 경우다. 검색 0건(`E-1001`), 한 기술만 근거 확보(`E-1004`). 근거 부족은 supervisor의 `evaluate_sufficiency`가 잡는다.
- 에러 코드는 기존 5개만, **하이픈 표기**(`E-1002`)로 쓴다.
- `node_result.error`는 라우팅용이다. 기존 규칙대로 `summary`/`uncertainty`/`limitations` 맨 앞에도 E-코드를 계속 남긴다.
- 보고서의 수집 오류 절은 기존 페이로드 안의 E-코드와 `control.node_errors`를 함께 모은다. 결과 없이 실패한 노드는 페이로드에 E-코드가 남지 않기 때문이다.

---

## 4. 품질 평가 (`quality` 노드)

### 4-1. 책임 범위

- Quality는 보고서를 네 항목으로 평가하고, 미달이면 **원인에 따라 조치를 권고**한다(`action`, `target_node`). 다음 노드는 supervisor가 정한다.

| `action` | 언제 | `target_node` |
|---|---|---|
| `pass` | `passed == True` | `""` |
| `rewrite` | 근거는 State에 있는데 보고서가 잘못 쓰거나 표시하지 않음: 표현·인용 연결·중립성·필수 목차·분량 문제 | `""` |
| `research` | State 자체에 그 관점 근거가 없거나("공개 근거 미확인") 한쪽 stance·단일 출처로 쏠려 보고서 수정만으로 해결할 수 없음 | 그 관점 노드(`market`·`stakeholder`·`domain`). 여러 개면 근거가 가장 적은 하나 |

- `research` 판단은 보고서 문장이 아니라 **State의 근거**(`perspective_evidence`, stance, 출처 수)로 확인한다. 같은 미달에 두 원인이 섞이면 `research`를 우선한다.
- 근거 최소 기준 판정은 여전히 supervisor의 `evaluate_sufficiency()`가 맡는다. Quality의 `research`는 그 기준은 통과했지만 보고서 품질 관점에서 부족이 드러난 경우의 추가 조사 요청이다.
- LangSmith에 `action`·`target_node`와 그 사유가 남아 "품질 평가 → 추가 조사" 판단이 트레이스에 보이게 한다.

### 4-2. 항목별 판정 (Hybrid)

| 항목 | 규칙 | LLM Judge |
|---|---|---|
| Groundedness | 대상 절(3장, 4-1~4-4, 5장)의 인용 `[n]`이 모두 REFERENCE 절 항목과 연결되고, 근거 항목(근거 목록·표의 근거 칸)에 인용이 있음 | 인용된 문장이 해당 Evidence `claim`과 의미상 맞는지 |
| Neutrality | 금지 표현(추천·우열 판정 어휘) 없음 | 문맥상 우열·추천 암시가 없는지 |
| Bias control | State에 negative 근거가 있는 관점·기술은 보고서도 negative 근거를 인용함. 서로 다른 출처가 2개 이상 확보된 관점·기술은 한 출처만 인용하지 않음 | 서술이 한쪽 근거로 기울지 않았는지 |
| Perspective coverage | 4관점(TRL·시장성·이해관계자·도메인) × 2기술 각각에 서술이 있거나 "공개 근거 미확인"이 명시됨 | 형식적 한 줄이 아니라 실질적으로 평가했는지 |

- Groundedness 규칙 검사에서 코드가 쓰는 고정 문단은 제외한다: 6장 방법론, 충분성 미달 사유 목록, 수집 오류 목록, TRL 추정 명시 문구, REFERENCE 아래 설명.
- 규칙 검사는 "인용 연결"까지만 보장한다. 출처가 주장을 실제로 뒷받침하는지는 Judge가 본다. README에도 이 구분을 적는다.
- 인용-출처 대조는 Markdown의 REFERENCE 절을 파싱해 `[n]`과 맞춘다.
- LLM Judge는 **1회 호출**로 네 항목을 함께 판정한다. `llm.structured`, `config.JUDGE_MODEL`, `config.TEMPERATURE`를 쓴다.
- Judge 입력은 SUMMARY, 4-1~4-4, 5장 본문과, 그 절에서 인용한 Evidence의 `claim` 목록으로 제한한다(전문 금지).
- 쪽수는 `report_path` PDF에서 센다.
- 필수 목차 형식 검사: Markdown에 SUMMARY 절과 REFERENCE 절이 있어야 한다(`required_sections_passed`).
- `feedback`에는 미달 항목별 고칠 점을 한국어로 적는다. `report`는 재작성할 때 이를 반영하고 분량이 늘지 않게 한다.
- LangSmith에 남는 출력: 항목별 규칙·Judge 통과 여부, 미달 사유, 쪽수, 필수 목차 여부, 평가한 보고서 버전.

---

## 5. 상한 (`config.py`)

```python
MAX_EXEC_RETRY = 1       # 노드 실행 실패 시 재시도 횟수 (7개 노드 공통)
MAX_AGENT_RETRY = 2      # 관점 노드 하나의 근거 부족 재조사 횟수
MAX_REPORT_RETRY = 1     # 품질 미달 시 보고서 재작성 횟수
MAX_QUALITY_RESEARCH = 1 # 품질 평가 기반 재조사 횟수 (실행 전체, evidence 재조사와 별도)
MAX_TOTAL_STEPS = 24     # 하위 노드 실행 횟수 안전장치. 도달하면 마무리 모드
MAX_REPORT_PAGES = 10    # 보고서 최대 쪽수
RECURSION_LIMIT = 2 * (MAX_TOTAL_STEPS + 3) + 10   # LangGraph recursion_limit (supervisor 방문 포함)
```

- 기존 `MAX_RETRY`는 삭제한다(`MAX_AGENT_RETRY`가 대체).
- 실패가 없을 때 최악의 정상 경로는 하위 노드 19회다(research 1 + 관점 3×3 + synthesis·report·quality 3 + 품질 기반 재조사 1과 synthesis·report·quality 3 + 재작성 report·quality 2).
- 상한 도달은 PASS가 아니라 `run_status = "exhausted"`다.
- 스텝 상한은 보고서 진입 조건이 아니다. 보고서로 넘어가는 조건은 항상 충분성 평가 결과이고, 상한은 실행 실패가 반복될 때만 작동하는 안전장치다.
- `app.py`는 `graph.invoke(..., config={"recursion_limit": config.RECURSION_LIMIT, ...})`로 넘긴다.

---

## 6. 관측성 (LangSmith)

- `.env`: `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`. 키는 `.env`에만 둔다(`.env.example`에는 이름만).
- `app.py`는 `config={"metadata": {"trace_id": control.trace_id}, "recursion_limit": ...}`로 실행한다.
- State의 `control.trace_id`와 LangSmith `metadata.trace_id`로 실행을 서로 찾는다.
- `app.py` 완료 메시지에 `trace_id`, `run_status`, 품질 결과(통과/미달/미실행), 재작업 횟수를 출력한다. 보고서는 품질 평가보다 먼저 만들어지므로 품질 결과는 보고서 안에 넣지 않는다.
- 재현성 범위: 라우팅은 같은 State면 같은 경로를 낸다. 웹 검색 시점과 LLM 출력은 비결정적이라 재작업 횟수·품질 결과는 실행마다 다를 수 있다. 종료와 보고서 생성은 상한으로 보장한다. README에 이 범위를 적는다.
- `--dummy`는 연결 확인용이다. 제출 캡처는 **실제 전체 실행이면서 재작업이 1회 이상 있는 실행**으로 고른다. 파일명은 `tracing-1.png`, `tracing-2.png`처럼 순서로 구분한다.

---

## 7. 재개/복구

- 실행 중 복구 판단에 필요한 최소 상태(`node_status`, `node_errors`, 재시도 카운터, `dispatch_id`, `run_status`)를 `control`에 둔다.
- 프로세스 종료 후 영속 재개(SQLite 등 persistent checkpointer)는 이번 범위에서 제외한다. README에 이 사실을 적는다.

---

## 8. 테스트

- `tests/test_supervisor.py`: 라우팅 함수가 State에 따라 다른 경로를 고르는지 검증한다. 최소 사례:
  - 최초 진입(`next_node == ""`)은 누락으로 판정하지 않음
  - `tech_summary`가 이미 있으면 `research`를 건너뜀
  - 근거가 적은 관점이 먼저 선택됨 / 동점이면 동점 처리 순서
  - `dispatch_id`가 다른 오래된 `node_result`는 `failed`로 판정
  - `skipped` 노드는 다시 선택되지 않음
  - 마무리 모드에서 각 노드 1회 시도 후 `END`
  - 보고서 재작성 후 `report_version`이 바뀌면 `quality` 재실행
  - `action == "research"`이면 `target_node` 재조사 → `synthesis` → `report` → `quality` 순으로 다시 돌고, 기회 소진 후에는 보고서 재작성으로 내려감
- `python app.py --dummy`가 끝까지 실행되어야 한다.

---

## 9. 범위 제외

- SQLite 영속 복구, 새 checkpointer 의존성
- State 대규모 구조 개편(기존 필드 이름·타입 변경)
- `references` Reducer 재설계, 보고서 hash
- 기존 RAG·웹 검색·TRL 판정 로직 개선
- 라우팅용 LLM, 병렬 fan-out

---

## 10. 이 계약으로 바뀌는 파일

| 파일 | 변경 |
|---|---|
| `docs/RAG-Design_v6.md` (신규) | 패턴·State(D-1)·그래프(D-2)·품질 평가 반영 |
| `docs/DEV_PLAN.md` | 노드 계약·루프·상한 갱신 |
| `state.py` | `NodeResult`·`MetricResult`·`QualityResult`·`ControlState`, `report_md_path`·`report_version`·`node_result`·`quality_result`·`control` 추가, `retry_count` 삭제, `make_initial_state()` 갱신 |
| `config.py` | 5장 상한 추가, `MAX_RETRY` 삭제 |
| `graph.py` | hub-and-spoke 그래프, `route_after_check` 삭제 |
| `app.py` | `trace_id`·`recursion_limit` 전달, 완료 메시지(6장) |
| `orchestration/supervisor.py` (신규) | 1장 규칙 구현 |
| `agents/quality.py` (신규) | 4장 구현(`action`·`target_node` 판정 포함), `prompts/quality*.md` |
| `agents/research.py`·`market.py`·`stakeholder.py`·`domain.py`·`synthesis.py` | `node_result`(dispatch_id 포함) 반환 추가 |
| `agents/report.py` | `report_md_path`·`report_version`·`node_result` 반환, `quality_result.feedback` 반영, 수집 오류 절에 `control.node_errors` 포함 |
| `agents/check.py` | `check_node`는 그래프에서 빠짐. `retry_count` 증가 전제 테스트 수정 |
| `tests/fixtures.py` | `DUMMY_NODES`가 `node_result` 반환, quality 더미 추가(`action`·`target_node` 포함), 샘플 State에 `control`·`report_version` |
| `tests/test_supervisor.py` (신규) | 8장 사례 |
| `.env.example` | LangSmith 변수 이름 추가 |
| `AGENTS.md`·`CLAUDE.md` | 새로 작성. 이 문서를 참조하고, LLM Judge를 범위에 포함, 담당 표에 supervisor·quality 추가 |
| `README.md` | 패턴·State 설계 근거·디렉토리 구조·Contributors·Groundedness 규칙/의미 검증 구분·재개 범위 제외 |
