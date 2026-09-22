주어진 웹 검색 Evidence만 이용해 {technology}의 이해관계자 관점을 분류하라.

반드시 competitors, adopters_devs, investors 세 목록을 반환한다.

- Evidence의 claim, source_id, stance를 변경하거나 새로 만들지 않는다.
- 각 목록에는 제공된 Evidence 항목만 그대로 선택한다.
- 관련 Evidence가 없으면 해당 목록은 빈 리스트로 둔다.
- positive, negative, neutral 이외의 stance를 사용하지 않는다.
- 충분성, 재조사, 다음 node, 기술 우열을 판단하지 않는다.
- summary 필드는 형식 충족용으로만 작성한다. 최종 summary는 Python이 검증된 claim으로 생성한다.

Evidence:
{evidence}
