from rag.prompts import multiturn
from rag.prompts.multiturn import (
    latest_user_question,
    normalize_message_content,
    select_refinement_messages,
)


def test_normalize_message_content_keeps_only_text_blocks():
    content = [
        {"type": "text", "text": "第一段"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AA=="},
        },
        {"type": "input_text", "text": "第二段"},
    ]

    assert normalize_message_content(content) == "第一段\n第二段"


def test_latest_user_question_skips_non_user_and_empty_messages():
    messages = [
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "上一答"},
        {
            "role": "user",
            "content": [{"type": "text", "text": "当前问题"}],
        },
    ]

    assert latest_user_question(messages) == "当前问题"


def test_select_refinement_messages_keeps_current_and_three_prior_turns():
    messages = [{"role": "system", "content": "system"}]
    for index in range(5):
        messages.extend(
            [
                {"role": "user", "content": f"问题{index}"},
                {"role": "assistant", "content": f"回答{index}"},
            ]
        )
    messages.append({"role": "user", "content": "当前问题"})

    assert select_refinement_messages(messages) == [
        {"role": "user", "content": "问题2"},
        {"role": "assistant", "content": "回答2"},
        {"role": "user", "content": "问题3"},
        {"role": "assistant", "content": "回答3"},
        {"role": "user", "content": "问题4"},
        {"role": "assistant", "content": "回答4"},
        {"role": "user", "content": "当前问题"},
    ]


def test_select_refinement_messages_drops_older_whole_messages_at_budget(
    monkeypatch,
):
    monkeypatch.setattr(
        multiturn,
        "num_tokens_from_string",
        lambda text: len(text),
    )
    messages = [
        {"role": "user", "content": "1111"},
        {"role": "assistant", "content": "2222"},
        {"role": "user", "content": "3333"},
    ]

    assert select_refinement_messages(messages, token_budget=8) == [
        {"role": "assistant", "content": "2222"},
        {"role": "user", "content": "3333"},
    ]


def test_select_refinement_messages_returns_current_only_when_it_exhausts_budget(
    monkeypatch,
):
    monkeypatch.setattr(
        multiturn,
        "num_tokens_from_string",
        lambda text: len(text),
    )
    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "current-question"},
    ]

    assert select_refinement_messages(messages, token_budget=5) == [
        {"role": "user", "content": "current-question"},
    ]
