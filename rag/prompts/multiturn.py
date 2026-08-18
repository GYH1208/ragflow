import datetime
import json
import logging
from dataclasses import dataclass
from enum import Enum
import re

import json_repair
from jinja2.sandbox import SandboxedEnvironment

from common.token_utils import num_tokens_from_string
from rag.prompts.template import load_prompt

REFINEMENT_TOKEN_BUDGET = 2048
REFINEMENT_CONFIDENCE_THRESHOLD = 0.75
OBVIOUS_REFERENCE_PATTERN = re.compile(
    r"(?:这个|那个|它|前者|后者|第一个|第二个|上述|刚才那个|这项|那项|这份|那份)"
)
ELLIPSIS_PATTERN = re.compile(r"^.{0,20}(?:呢|怎么样|如何)[？?]?$", re.DOTALL)
PROMPT_ENV = SandboxedEnvironment(
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)
MULTITURN_REFINEMENT_PROMPT = load_prompt("multiturn_refinement_prompt")


class RefinementAction(str, Enum):
    REWRITE = "rewrite"
    USE_ORIGINAL = "use_original"
    CLARIFY = "clarify"


@dataclass(frozen=True)
class QuestionRefinementDecision:
    action: RefinementAction
    question: str
    confidence: float
    unresolved_references: tuple[str, ...] = ()
    clarification_question: str = ""
    used_fallback: bool = False


def normalize_message_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") in {"text", "input_text"}
                and block.get("text")
            ):
                texts.append(str(block["text"]))
            elif isinstance(block.get("text"), (str, int, float)):
                texts.append(str(block["text"]))
        return "\n".join(texts).strip()
    return str(content).strip()


def latest_user_question(messages: list[dict]) -> str:
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        content = normalize_message_content(message.get("content"))
        if content:
            return content
    return ""


def select_refinement_messages(
    messages: list[dict],
    *,
    token_budget: int = REFINEMENT_TOKEN_BUDGET,
    max_user_messages: int = 4,
) -> list[dict[str, str]]:
    normalized = []
    for index, message in enumerate(messages or []):
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = normalize_message_content(message.get("content"))
        if content:
            normalized.append((index, {"role": role, "content": content}))

    user_positions = [
        index for index, item in normalized if item["role"] == "user"
    ]
    if not user_positions:
        return []

    selected_user_count = min(len(user_positions), max(1, max_user_messages))
    first_index = user_positions[-selected_user_count]
    last_index = user_positions[-1]
    candidates = [
        item
        for index, item in normalized
        if first_index <= index <= last_index
    ]

    selected_reversed = [candidates[-1]]
    used_tokens = num_tokens_from_string(candidates[-1]["content"])
    for item in reversed(candidates[:-1]):
        token_count = num_tokens_from_string(item["content"])
        if used_tokens + token_count > token_budget:
            break
        selected_reversed.append(item)
        used_tokens += token_count

    return list(reversed(selected_reversed))


def has_obvious_unresolved_reference(question: str) -> bool:
    text = (question or "").strip()
    return bool(
        OBVIOUS_REFERENCE_PATTERN.search(text)
        or ELLIPSIS_PATTERN.fullmatch(text)
    )


def _generic_clarification(question: str) -> str:
    if re.search(r"[\u3400-\u9fff]", question or ""):
        return "我还不能确定你指的是前面对话中的哪个对象，请补充具体名称或选项。"
    return (
        "I cannot determine which earlier item you mean. "
        "Please provide its name or option."
    )


def _fallback_decision(
    original_question: str,
) -> QuestionRefinementDecision:
    if has_obvious_unresolved_reference(original_question):
        return QuestionRefinementDecision(
            RefinementAction.CLARIFY,
            original_question,
            0.0,
            (),
            _generic_clarification(original_question),
            True,
        )
    return QuestionRefinementDecision(
        RefinementAction.USE_ORIGINAL,
        original_question,
        0.0,
        used_fallback=True,
    )


def parse_refinement_response(
    raw: str,
    original_question: str,
) -> QuestionRefinementDecision:
    try:
        payload = json_repair.loads(raw)
    except Exception:
        return _fallback_decision(original_question)
    if not isinstance(payload, dict):
        return _fallback_decision(original_question)

    action_value = payload.get("action")
    confidence = payload.get("confidence")
    standalone = payload.get("standalone_question")
    unresolved = payload.get("unresolved_references")
    clarification = payload.get("clarification_question")
    if action_value not in {item.value for item in RefinementAction}:
        return _fallback_decision(original_question)
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 <= confidence <= 1.0
    ):
        return _fallback_decision(original_question)
    if not isinstance(standalone, str) or len(standalone) > 2000:
        return _fallback_decision(original_question)
    if not isinstance(clarification, str) or len(clarification) > 300:
        return _fallback_decision(original_question)
    if (
        not isinstance(unresolved, list)
        or len(unresolved) > 8
        or any(
            not isinstance(item, str) or len(item) > 100
            for item in unresolved
        )
    ):
        return _fallback_decision(original_question)

    action = RefinementAction(action_value)
    unresolved_tuple = tuple(item for item in unresolved if item)
    if unresolved_tuple:
        return QuestionRefinementDecision(
            RefinementAction.CLARIFY,
            original_question,
            float(confidence),
            unresolved_tuple,
            clarification.strip() or _generic_clarification(original_question),
        )
    if action is RefinementAction.CLARIFY:
        return _fallback_decision(original_question)
    if action is RefinementAction.USE_ORIGINAL:
        return QuestionRefinementDecision(
            action,
            original_question,
            float(confidence),
        )
    if (
        confidence < REFINEMENT_CONFIDENCE_THRESHOLD
        or not standalone.strip()
    ):
        return _fallback_decision(original_question)
    return QuestionRefinementDecision(
        action,
        standalone.strip(),
        float(confidence),
    )


async def refine_multiturn_question(
    chat_mdl,
    messages: list[dict],
    language: str | None = None,
) -> QuestionRefinementDecision:
    original_question = latest_user_question(messages)
    history = select_refinement_messages(messages)
    if not original_question:
        raise ValueError("The current user message has no textual content.")
    if sum(item["role"] == "user" for item in history) < 2:
        return QuestionRefinementDecision(
            RefinementAction.USE_ORIGINAL,
            original_question,
            1.0,
        )

    today = datetime.date.today()
    system_prompt = PROMPT_ENV.from_string(
        MULTITURN_REFINEMENT_PROMPT
    ).render(
        today=today.isoformat(),
        yesterday=(today - datetime.timedelta(days=1)).isoformat(),
        tomorrow=(today + datetime.timedelta(days=1)).isoformat(),
        language=language,
        conversation_json=json.dumps(history, ensure_ascii=False),
    )
    try:
        raw = await chat_mdl.async_chat(
            system_prompt,
            [{"role": "user", "content": "Output JSON only."}],
            {"temperature": 0.1},
        )
        if isinstance(raw, tuple):
            raw = raw[0]
        if "**ERROR**" in raw:
            return _fallback_decision(original_question)
        cleaned = re.sub(r"^.*</think>", "", raw, flags=re.DOTALL)
        return parse_refinement_response(cleaned, original_question)
    except Exception:
        logging.exception("Multiturn question refinement failed")
        return _fallback_decision(original_question)
