주어진 웹 검색 Evidence만 이용해 {technology}의 이해관계자 관점을 분류하라.

반드시 competitors, adopters_devs, investors 세 목록을 반환한다.

{stance_instruction}
- claim이나 source_id를 복사하거나 반환하지 않는다. 제공된 목록의 id만 선택한다.
- 새 id를 만들지 않는다. 각 목록에는 제공된 Evidence id만 선택한다.
- 관련 Evidence가 없으면 해당 목록은 빈 리스트로 둔다.
- positive, negative, neutral 이외의 stance를 사용하지 않는다.
- 기술 우열이나 추천을 판단하지 않으며, 근거 없는 항목을 선택하지 않는다.
- 충분성, 재조사, 다음 node, 기술 우열을 판단하지 않는다.
- summary 필드는 형식 충족용으로만 작성한다. 최종 summary는 Python이 검증된 claim으로 생성한다.

Evidence:
{evidence}
