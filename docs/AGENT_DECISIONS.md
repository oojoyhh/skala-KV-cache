# Agent 과제 주요 결정 기록

Agent 과제(Multi-Agent Orchestration)에서 팀이 내린 큰 결정과 그 이유를 남긴다.
구현 계약 전문은 `docs/AGENT_CONTRACT.md`가 기준이다. 이 문서는 "왜 그렇게 정했는지"를 기록한다.

- 결정일: 2026-10-07
- 결정 방식: 팀 논의 (제안서 → 피드백 2회 → 계약 v1 → 피드백 fix1~fix4 → 계약 v2)

---

## D1. 패턴: Supervisor

- **결정:** Supervisor 패턴을 쓴다.
- **검토한 대안:** Orchestrator-Workers.
- **이유**
  - 기존 그래프에 충분성 검사, 관점별 재조사, 재시도 상한이 이미 있다. "근거 충분성 판단 → 부족 관점 재작업" 흐름이 Supervisor와 그대로 맞는다.
  - Orchestrator-Workers로 가려면 계획 노드, 구조화된 서브태스크 목록, `Send` 기반 동적 fan-out, 실패 fallback을 새로 만들어야 한다. 관점 4개가 이미 정해져 있어서 "계획 후 Worker 결정"을 억지로 만들어야 한다.
  - 짧은 개발 시간 안에 완성도를 확보하기 쉽다.

## D2. 라우팅: hub-and-spoke, 규칙 기반, 한 번에 하나

- **결정**
  - 모든 하위 노드(research·market·stakeholder·domain·synthesis·report·quality)는 실행 후 supervisor로 돌아온다. 하위 노드 간 직접 edge는 없다.
  - supervisor는 State 값으로 다음 노드를 고르는 규칙 함수다(LLM 라우팅 아님).
  - 한 번에 `next_node` 하나만 고른다. 관점 평가는 병렬이 아니라 순차 실행이다.
  - 관점 후보가 여럿이면 **근거가 가장 적은 관점부터** 고르고, 개수가 같을 때만 `market → stakeholder → domain` 순으로 정한다.
- **검토한 대안:** LLM 라우팅, 규칙+LLM 혼합 / 관점 3개 병렬 fan-out 유지 / `synthesis → report → quality` 직접 연결 / 고정 우선순위(`market → stakeholder → domain`)로 선택.
- **이유**
  - 규칙 기반: 같은 State면 같은 경로가 나와서 재현성과 종료 보장을 코드로 설명할 수 있다.
  - 순서 하드코딩 아님: 후보는 State(미실행·근거 부족·재시도 여유)로 정해지고, 고정 순서는 동점 처리에만 쓴다. 정상 실행의 경로가 늘 같아 보이는 건 State가 같기 때문이며, State가 다르면 경로가 달라진다. 이를 `tests/test_supervisor.py`로 증명한다.
  - 동적 동작을 보여주는 장치: 매 결정마다 `route_reason`을 남긴다. 제출 트레이스는 재작업이 1회 이상 있는 실제 실행으로 고른다.
  - 순차 실행: 실행 시간은 늘지만, Supervisor의 판단이 트레이스에 한 단계씩 드러난다. 동시 쓰기가 없어 병합(Reducer) 문제가 줄어든다.
  - 직접 연결 금지: synthesis·report·quality까지 supervisor를 거쳐야 "하위 에이전트는 Supervisor와만 통신" 요구에 확실히 맞는다.

## D3. 충분성 판정은 supervisor 내부 함수 호출

- **결정:** `agents.check.evaluate_sufficiency(state)`를 supervisor 안에서 호출한다. `check` 노드는 그래프에서 뺀다.
- **검토한 대안:** `supervisor → check → supervisor` 경로로 별도 노드 유지.
- **이유:** "Supervisor가 근거 충분성을 평가한다"는 요구에 직접 맞고 그래프가 단순해진다. 판정 로직은 기존 코드를 그대로 재사용한다.
- **재조사 상한 도달 시:** 부족한 채로 종합·보고서로 진행하되, 부족 사유를 synthesis가 `limitations`에 옮기고 `E-1005`로 명시한다. 충분성 평가를 거친 뒤 진행한다는 점은 같다.

## D4. State: 제어 / 실행 결과 / 평가 / 페이로드 분리

- **결정**
  - 기존 Agent 페이로드 키는 이름·타입을 그대로 둔다. `report_path`도 유지하고 `report_md_path`·`report_version`만 추가한다.
  - 신규 제어값은 `control`(ControlState) 하나에 묶고, supervisor만 수정한다. 하위 노드는 `control`을 읽기만 한다.
  - 하위 노드는 실행 성공/실패를 `node_result`(NodeResult)로 보고한다.
  - 품질 평가 결과는 `quality_result`(QualityResult)에 둔다. 항목마다 규칙·Judge 판정과 사유를 따로 남긴다.
  - 단일 `retry_count`는 없애고 용도별 카운터 셋으로 나눈다(D5).
- **검토한 대안:** 에이전트가 `summary`의 E-코드로 실패를 알리고 supervisor가 해석 / 에이전트도 `control` 일부를 직접 수정 / `retry_count`와 새 카운터 병행 / `QualityResult`를 boolean만으로 구성.
- **이유**
  - `node_result`: 업무 결과와 실행 상태가 섞이지 않는다. 각자 AI로 코드를 써도 "하위 노드는 자기 페이로드+`node_result`, supervisor는 `control`"로 책임이 분명하다.
  - E-코드는 `summary` 등에도 계속 남긴다. 그 값은 보고서의 수집 오류 절이 읽는 내용이고, `node_result.error`는 라우팅용이라 읽는 쪽이 다르다.
  - 항목별 `MetricResult`: 규칙과 Judge 중 어느 쪽이, 왜 실패했는지 트레이스와 발표에서 바로 설명할 수 있다.
  - `references`의 기존 Reducer(`operator.add`)는 유지한다. 지우면 노드마다 출처가 덮어써진다.

### State 설계 판정 포인트별 근거

| 포인트 | 우리 설계 |
|---|---|
| 제어 vs 페이로드 분리 | 라우팅에 필요한 최소 상태만 `control`에 두고, 결과는 기존 페이로드 키에 둔다. 실행 성공/실패는 `node_result`로 따로 전달한다 |
| 관측성 위치 | 결정 로그는 State가 아니라 외부 트레이스(LangSmith)에 둔다. State에는 마지막 `route_reason`만 남긴다. Supervisor 실행마다 LangSmith에 `{trace_id: metadata.trace_id, node: 직전 노드, decision: next_node, reason: route_reason, ts: LangSmith 자동 기록}`이 남아 결정과 사유가 함께 명시된다 |
| 지속성 비용 | 보고서 전문을 State에 넣지 않고 `report_path`·`report_md_path`만 둔다. 결정 이력은 State에 쌓지 않는다. `references`는 재조사 때 늘어나지만 재시도 상한으로 증가량이 제한되고, 중복은 보고서에서 `source_id`로 제거한다 |
| 상관 | `control.trace_id` = LangSmith `metadata.trace_id`. 노드 호출과 결과는 `dispatch_id`로 짝지어진다 |
| 재개/복구 | `node_status`·`node_errors`·재시도 카운터·`dispatch_id`·`run_status`로 실행 중 복구 판단. 영속 재개는 범위 제외(D8) |
| 동시 처리 | 순차 라우팅이라 동시 쓰기가 없다. `references`만 기존 `operator.add` Reducer 유지 |
| 종료 보장 | 용도별 재시도 상한, `MAX_TOTAL_STEPS` 도달 시 마무리 모드(노드별 1회), LangGraph `recursion_limit` |

## D5. 실패 처리(fallback)와 결과 검증

- **결정**
  - `failed`는 실행 자체가 안 된 경우(예외, `E-1002`, `E-1003`)만 해당한다. 검색 0건(`E-1001`)이나 한 기술만 근거 확보(`E-1004`)는 `success`로 두고 충분성 판정이 잡는다.
  - 재시도 카운터를 용도별로 나눈다.
    - `exec_retry_counts`: 실행 실패, 7개 노드 모두, 상한 `MAX_EXEC_RETRY`
    - `evidence_retry_counts`: 근거 부족 재조사, market·stakeholder·domain, 상한 `MAX_AGENT_RETRY`
    - `report_retry_count`: 품질 미달 재작성, 상한 `MAX_REPORT_RETRY`
  - 실행 재시도 상한에 닿은 노드는 `skipped`로 두고 **어떤 규칙에서도** 다시 고르지 않는다. 조사 노드는 제외하고 진행하고, synthesis·report·quality는 `END`(`exhausted`)로 끝낸다.
  - supervisor가 노드를 부를 때 `dispatch_id`(= 그 시점의 `step_count`)를 발급하고, 노드는 `node_result.dispatch_id`로 그대로 돌려준다. 노드 이름과 `dispatch_id`가 모두 맞아야 유효한 결과로 본다.
  - 최초 진입(`next_node == ""`)에서는 결과 검증을 건너뛴다.
  - 노드별 오류는 `control.node_errors`에 남기고, 보고서의 수집 오류 절이 이 값도 읽는다.
- **검토한 대안:** 실패 재시도와 근거 부족 재조사를 한 카운터로 합산 / `node_result`에 노드 이름만 확인 / uuid `dispatch_id` / `node_status`에 `running` 상태 추가 / `last_error` 하나만 유지.
- **이유**
  - 카운터를 합치면 synthesis·report·quality 실패를 셀 곳이 없고(`KeyError`), 실패 재시도가 재조사 기회를 깎는다.
  - 같은 노드를 두 번 부를 때 이전 `node_result`가 남아 있으면, 노드가 반환을 빠뜨려도 성공으로 읽힌다. `dispatch_id`로 막는다. 정수를 쓰면 uuid보다 재현성이 좋고, `running` 상태 없이도 판별된다.
  - 최초 진입은 결과가 없는 게 정상이라 누락으로 오판하면 안 된다.
  - `last_error` 하나로는 여러 노드가 실패했을 때 앞의 오류가 사라진다.

## D6. 품질 평가: 항목별 Hybrid, 보고서 표현만 책임

- **결정**
  - 네 항목 모두 **규칙 검사 + LLM Judge**로 판정한다. Judge는 1회 호출로 네 항목을 함께 본다.
    - Groundedness: 규칙은 인용 `[n]`과 REFERENCE의 연결, 근거 항목의 인용 여부. Judge는 문장과 Evidence `claim`의 의미 일치.
    - Neutrality: 규칙은 금지 표현. Judge는 문맥상 우열·추천 암시.
    - Bias control: 규칙은 확보된 negative 근거·복수 출처를 보고서가 인용했는지. Judge는 서술의 편향.
    - Perspective coverage: 규칙은 4관점 × 2기술 각각 서술 또는 "공개 근거 미확인" 명시. Judge는 실질적 평가 여부.
  - Groundedness 규칙은 사실 주장 절(3장, 4-1~4-4, 5장)만 본다. 코드가 쓰는 고정 문단은 제외한다.
  - 평가 원본은 보고서 Markdown(`report_md_path`)이다. Judge 입력은 SUMMARY·4-1~4-4·5장과 인용된 Evidence `claim`으로 제한한다. PDF는 쪽수(10쪽 이하)만 확인한다.
  - 형식 검사로 필수 목차(SUMMARY, REFERENCE) 절이 있는지 확인한다. 없으면 미달이다.
  - **Quality는 보고서가 확보된 근거를 올바르게 사용·표시했는지만 평가한다.** 근거 충분성은 supervisor의 `evaluate_sufficiency`가 맡는다. 미달 시 재조사하지 않고 보고서만 재작성한다.
  - quality는 `quality_result`만 쓴다. 재작성 여부는 supervisor가 정한다. report는 `feedback`을 반영하되 분량이 늘지 않게 한다.
  - 보고서 버전(`report_version`)과 평가 버전(`evaluated_report_version`)이 다르면 quality를 다시 실행한다.
- **검토한 대안:** 형식 검사만 / LLM Judge만 / Groundedness·Coverage는 규칙만, Neutrality·Bias는 Judge만 / 미달 항목에 따라 재조사로 되돌림 / "인용 없는 주장 문장 없음"을 규칙으로 판정 / 보고서 전문을 State에 저장.
- **이유**
  - 항목마다 규칙으로 잴 수 있는 부분은 결정적으로 판정하고, 의미 판단만 Judge에 맡긴다. 규칙만 쓰면 의미상 틀린 인용이 통과하고, Judge만 쓰면 결과가 흔들린다.
  - "인용 없는 주장"은 정규식으로 판별할 수 없고, 코드가 쓰는 고정 문단에는 인용이 없어 항상 미달이 된다. 그래서 규칙은 "인용 연결"까지만 보장하고, 실제 뒷받침 여부는 Judge가 본다고 구분한다.
  - 근거가 0건인 관점은 보고서가 "공개 근거 미확인"으로 적는다. 이를 미달로 보면 재작성해도 영구 미달이고, 근거를 만들어내지 않는다는 원칙과 충돌한다.
  - 재조사(규칙 5)가 종합보다 먼저 돌기 때문에, 품질 평가 시점에 부족한 관점은 이미 재조사 상한을 다 쓴 상태다. 품질 미달로 재조사로 되돌려도 실행되는 경우가 거의 없고, 종합·보고서 무효화 규칙까지 필요해진다.
  - 보고서 Markdown은 이미 PDF와 함께 생성되고 있어 State를 키우지 않고 평가할 수 있다.

## D7. 상한값과 종료

| 설정 | 값 | 의미 |
|---|---|---|
| `MAX_EXEC_RETRY` | 1 | 노드 실행 실패 시 재시도 횟수 (7개 노드 공통) |
| `MAX_AGENT_RETRY` | 2 | 관점 노드 하나의 근거 부족 재조사 횟수 |
| `MAX_REPORT_RETRY` | 1 | 품질 미달 시 보고서 재작성 횟수 |
| `MAX_TOTAL_STEPS` | 20 | 하위 노드 실행 횟수 안전장치. 도달하면 마무리 모드 |
| `MAX_REPORT_PAGES` | 10 | 보고서 최대 쪽수 |
| `RECURSION_LIMIT` | `2 * (MAX_TOTAL_STEPS + 3) + 10` | LangGraph `recursion_limit` (supervisor 방문 포함) |

- **마무리 모드:** `MAX_TOTAL_STEPS`에 닿으면 조사와 재시도를 멈춘다. synthesis·report·quality 중 필요한 것만 **각 1회** 시도하고, 하나라도 실패하면 바로 `END`로 끝낸다.
- **검토한 대안:** 상한 도달 즉시 `END` / synthesis·report가 없으면 계속 선택 / 상한 도달 후에도 synthesis·report·quality 반드시 실행.
- **이유**
  - 즉시 `END`는 종료는 확실하지만 보고서나 품질 평가 없이 끝날 수 있다. 가이드는 보고서 뒤 품질 평가를 요구한다.
  - "없으면 계속 선택"은 해당 노드가 계속 실패하면 무한 반복된다.
  - 마무리 모드는 추가 실행이 최대 3회라 종료가 보장되고, 품질 평가도 실행된다.
- **스텝 수로 보고서 작성을 정하지 않는다.** 보고서로 넘어가는 조건은 항상 충분성 평가 결과다(충분, 또는 재조사 상한 도달). 실패가 없을 때 정상 경로는 최대 15회라 상한 20회에 닿지 않는다. 마무리 모드는 실행 실패가 반복될 때만 작동하는 안전장치다.
- 처음 제안은 `MAX_TOTAL_STEPS = 15`였다. 실패가 없을 때 최악의 정상 경로가 15회라서 여유를 두고 20으로 올렸다.
- 상한 도달은 PASS가 아니라 `run_status = "exhausted"`다.

## D8. 범위 제외

- SQLite 영속 복구(새 checkpointer 의존성). README에 "실행 중 복구 판단용 최소 상태는 State에 두고, 프로세스 종료 후 영속 재개는 범위 제외"라고 적는다.
- State 대규모 구조 개편: 기존 필드 이름 변경 금지.
- `references` Reducer 재설계, 보고서 hash.
- 기존 RAG·웹 검색·TRL 판정 로직 개선.
- 품질 미달 시 재조사(D6).

## D9. 관측성: LangSmith + trace_id

- **결정**
  - 실행 시작 시 `trace_id = uuid4().hex`를 만들어 `control.trace_id`와 LangSmith `metadata.trace_id`에 같은 값을 넣는다.
  - `app.py` 완료 메시지에 `trace_id`, `run_status`, 품질 결과(통과/미달/미실행), 재작업 횟수를 출력한다. 보고서는 품질 평가보다 먼저 만들어지므로 품질 결과는 보고서 안에 넣지 않는다.
  - `--dummy`는 연결 확인용이다. 제출 캡처는 실제 전체 실행이면서 재작업이 1회 이상 있는 실행으로 고른다.
- **이유:** 비용이 거의 없고, State와 외부 트레이스를 잇는 상관 키를 코드로 보여줄 수 있다. 재작업이 없는 트레이스는 고정 파이프라인과 구별되지 않는다.
- **재현성 범위:** 라우팅은 같은 State면 같은 경로를 낸다. 그러나 State를 만드는 입력에 비결정 요소가 있다. 웹 검색 결과는 검색 시점에 따라 바뀌고, LLM(생성·분류·Judge)은 `TEMPERATURE = 0`이어도 출력이 달라질 수 있다. 그래서 재작업 횟수와 품질 통과 여부는 제출 트레이스와 다를 수 있다. 대신 **무한 루프 없이 종료하고 보고서가 생성되는 것은 상한(D7)으로 보장**한다. README에 이 범위와 비결정 요소를 적는다.

## D10. 코드 구조: 조정 계층 분리

- **결정:** supervisor는 `orchestration/supervisor.py`에 두고, 하위 에이전트(`agents/`)와 폴더를 나눈다. 라우팅 코드는 후보 계산(`candidate_nodes`)과 동점 처리(`tie_break`)를 분리한다.
- **검토한 대안:** `agents/supervisor.py`.
- **이유:** 조정 계층과 하위 에이전트의 분리가 디렉토리 구조만 봐도 드러난다. README 디렉토리 구조와 그대로 맞춘다.

---

## 변경 이력

- **v1:** 팀 제안서와 피드백 2회를 반영해 최초 Freeze.
- **v2:** 팀 피드백 fix1~fix4를 반영.
  - 최초 진입 검증 제외, `dispatch_id` 도입, 재시도 카운터 3종 분리, `skipped` 전 규칙 적용(D5)
  - 마무리 모드로 종료 보장과 품질 평가 실행을 함께 보장(D7)
  - 관점 선택을 근거 부족 순 + 동점 처리로 변경(D2)
  - 품질 평가를 항목별 Hybrid로 바꾸고 Groundedness·Coverage 기준과 책임 범위를 조정. `report_version`, `MetricResult` 추가(D6)
  - `node_errors` 추가(D5), supervisor 위치를 `orchestration/`으로 변경(D10)
- **v2.1:** 가이드 재검토 반영. 스텝 상한이 보고서 진입 조건이 아님을 명시(D7), 재현성 범위 명시(D9), 필수 목차 형식 검사 추가(D6), 결정 로그 항목 대응 명시(D4).

## 미결정

- [ ] 역할 분담 (새로 정함)
- [ ] 새 과제 브랜치 이름(기존 작업과 브랜치로 구분)
- [ ] 설계서 `docs/RAG-Design_v6.md` 작성과 `docs/DEV_PLAN.md` 갱신
- [ ] 새 `AGENTS.md`·`CLAUDE.md` 작성
