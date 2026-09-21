"""공용 LLM 모듈 — 모든 에이전트는 LLM을 여기서만 가져온다.

- get_llm(role): ChatOpenAI 인스턴스 ("generator" = 문장 생성, "judge" = 분류·판정)
- generate(prompt, role): 문자열 응답
- structured(prompt, response_model, role): Pydantic 모델로 응답 (구조화 출력)
- StructuredClient: 4번의 StructuredOutputClient.invoke(prompt, response_model) 형태에 맞춘 어댑터

외부 호출 실패는 여기서 삼키지 않고 LLMError로 올린다. 노드는 이를 잡아 빈 결과 + "[E-1002] ..." 로 처리한다 (DEV_PLAN §6).
"""

from functools import lru_cache
from typing import Literal, TypeVar

from pydantic import BaseModel

import config

Role = Literal["generator", "judge"]
M = TypeVar("M", bound=BaseModel)


class LLMError(RuntimeError):
    """LLM 호출 실패 (E-1002)."""


@lru_cache(maxsize=None)
def get_llm(role: Role = "generator"):
    from langchain_openai import ChatOpenAI

    model = config.GENERATOR_MODEL if role == "generator" else config.JUDGE_MODEL
    return ChatOpenAI(model=model, temperature=config.TEMPERATURE)


def generate(prompt: str, role: Role = "generator") -> str:
    try:
        return get_llm(role).invoke(prompt).content
    except Exception as exc:  # noqa: BLE001 — 외부 API 오류를 한 종류로 통일
        raise LLMError(f"[E-1002] LLM 호출 실패({role}): {exc}") from exc


def structured(prompt: str, response_model: type[M], role: Role = "judge") -> M:
    try:
        return get_llm(role).with_structured_output(response_model).invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"[E-1002] 구조화 출력 실패({role}, {response_model.__name__}): {exc}") from exc


class StructuredClient:
    """agents/stakeholder.py·domain.py의 StructuredOutputClient 프로토콜 구현체."""

    def __init__(self, role: Role = "judge"):
        self.role = role

    def invoke(self, prompt: str, response_model: type[M]) -> M:
        return structured(prompt, response_model, self.role)


def load_prompt(name: str) -> str:
    """prompts/<name>.md 를 읽는다 (DEV_PLAN §5)."""
    with open(f"prompts/{name}.md", encoding="utf-8") as f:
        return f.read()
