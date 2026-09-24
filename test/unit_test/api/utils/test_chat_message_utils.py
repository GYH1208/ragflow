def test_stamp_user_message_uses_server_time_without_mutating_input():
    from api.utils.chat_message_utils import stamp_user_message

    original = {
        "role": "user",
        "content": "current question",
        "created_at": 1,
    }

    stamped = stamp_user_message(original, created_at=1_800_000_000.5)

    assert original["created_at"] == 1
    assert stamped == {
        "role": "user",
        "content": "current question",
        "created_at": 1_800_000_000.5,
    }
