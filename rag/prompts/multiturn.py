from dataclasses import dataclass
from enum import Enum

from common.token_utils import num_tokens_from_string

REFINEMENT_TOKEN_BUDGET = 2048
REFINEMENT_CONFIDENCE_THRESHOLD = 0.75


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
