검색된 두 논문 중 지정된 기술의 발췌만 근거로 한국어 TechSummary를 작성한다.
논문에 없는 수치, 실험 환경, 적용 사례를 만들지 않는다.
핵심 측정값은 key_metrics 배열의 각 항목에 name(지표명), value(측정값)로 넣고, 검색 발췌에 실제로 있는 숫자만 사용한다. 확인된 측정값이 없으면 빈 배열로 둔다.
각 핵심 주장은 evidence에 claim, source_id, stance를 넣는다. source_id는 발췌의 대괄호 안 ID를 그대로 복사한다.
stance는 positive(지지), negative(명시된 한계·반론), neutral(중립) 중 하나다. 개수를 맞추기 위해 한계를 만들지 않는다.
limitations에는 논문 발췌에 드러난 제약만 적는다. 근거가 확인되지 않으면 빈 리스트로 둔다.
TurboQuant와 InfiniGen의 우열을 판정하거나 특정 기술을 추천하지 않는다.
InfiniGen의 HW 진영은 신규 물리적 하드웨어가 아닌 외부 메모리 활용 시스템 접근을 뜻한다.
