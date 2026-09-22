주어진 웹 검색 Evidence만 이용해 {domain} 적용 맥락의 도메인 근거를 분류하라.

반드시 cost, throughput, model_quality, transfer_overhead, deployment_barrier 다섯 목록을 반환한다.

- 제공된 Evidence의 claim, source_id, stance를 변경하거나 새로 만들지 않는다.
- Evidence가 직접 뒷받침하는 축에만 그대로 선택한다.
- 근거가 없는 축은 빈 리스트로 둔다. 모든 축을 채울 필요는 없다.
- 같은 Evidence는 실제로 여러 축을 뒷받침할 때만 변경 없이 재사용할 수 있다.
- 수치, 성능, 사실을 추가하지 않고 stance도 바꾸지 않는다.
- 충분성, 재조사, 다음 node, 기술 우열을 판단하지 않는다.
- summary 필드는 형식 충족용이다. 최종 summary는 Python이 검증된 claim으로 생성한다.

Evidence:
{evidence}
