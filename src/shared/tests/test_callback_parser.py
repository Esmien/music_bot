"""Unit-тесты typed parser для callback_data формата fb:action:gen_id."""

import pytest

from shared.callback_parser import FeedbackAction, parse_feedback_callback


def test_parse_like_with_gen_id():
    """Успешный парсинг fb:like:123."""
    result = parse_feedback_callback("fb:like:123")
    assert result is not None
    assert result.action == FeedbackAction.LIKE
    assert result.gen_id == 123


def test_parse_dislike_with_gen_id():
    """Успешный парсинг fb:dislike:456."""
    result = parse_feedback_callback("fb:dislike:456")
    assert result is not None
    assert result.action == FeedbackAction.DISLIKE
    assert result.gen_id == 456


def test_parse_send_with_gen_id():
    """Успешный парсинг fb:send:789."""
    result = parse_feedback_callback("fb:send:789")
    assert result is not None
    assert result.action == FeedbackAction.SEND
    assert result.gen_id == 789


def test_parse_finish_with_gen_id():
    """Успешный парсинг fb:finish:42."""
    result = parse_feedback_callback("fb:finish:42")
    assert result is not None
    assert result.action == FeedbackAction.FINISH
    assert result.gen_id == 42


@pytest.mark.parametrize(
    "callback_data",
    [
        "fb:like",  # отсутствующий gen_id
        "fb:dislike",  # отсутствующий gen_id
        "fb:like:abc",  # нечисловой gen_id
        "fb:dislike:1.5",  # нечисловой gen_id
        "fb:like:0",  # gen_id == 0
        "fb:dislike:-1",  # отрицательный gen_id
        "fb:like:-123",  # отрицательный gen_id
        "fb:like:1:extra",  # лишние сегменты
        "fb:dislike:2:extra:more",  # несколько лишних сегментов
        "like:123",  # отсутствует префикс fb:
        "fb:",  # только префикс
        "fb:unknown:123",  # некорректный action
        "",  # пустая строка
        None,  # None
    ],
)
def test_parse_malformed_callback(callback_data):
    """Malformed callback отклоняются и возвращают None."""
    result = parse_feedback_callback(callback_data)
    assert result is None


def test_parse_large_positive_gen_id():
    """Большой положительный gen_id парсится корректно."""
    result = parse_feedback_callback("fb:like:999999")
    assert result is not None
    assert result.action == FeedbackAction.LIKE
    assert result.gen_id == 999999


def test_parse_gen_id_one():
    """gen_id == 1 парсится корректно."""
    result = parse_feedback_callback("fb:like:1")
    assert result is not None
    assert result.action == FeedbackAction.LIKE
    assert result.gen_id == 1
