"""Тесты typed parser для callback_data."""

from shared.callback_parser import FeedbackAction, parse_feedback_callback


def test_parse_valid_like_with_gen_id():
    """Валидный callback с лайком и gen_id корректно разбирается."""
    result = parse_feedback_callback("fb:like:123")
    assert result is not None
    assert result.action == FeedbackAction.LIKE
    assert result.gen_id == 123


def test_parse_valid_dislike_without_gen_id():
    """Валидный callback с дизлайком без gen_id корректно разбирается."""
    result = parse_feedback_callback("fb:dislike")
    assert result is not None
    assert result.action == FeedbackAction.DISLIKE
    assert result.gen_id is None


def test_parse_valid_send_with_gen_id():
    """Валидный callback с отправкой отзыва и gen_id корректно разбирается."""
    result = parse_feedback_callback("fb:send:456")
    assert result is not None
    assert result.action == FeedbackAction.SEND
    assert result.gen_id == 456


def test_parse_valid_finish_with_gen_id():
    """Валидный callback с завершением и gen_id корректно разбирается."""
    result = parse_feedback_callback("fb:finish:789")
    assert result is not None
    assert result.action == FeedbackAction.FINISH
    assert result.gen_id == 789


def test_parse_malformed_missing_prefix():
    """Callback без префикса fb: возвращает None."""
    result = parse_feedback_callback("like:123")
    assert result is None


def test_parse_malformed_empty_string():
    """Пустая строка возвращает None."""
    result = parse_feedback_callback("")
    assert result is None


def test_parse_malformed_none():
    """None возвращает None."""
    result = parse_feedback_callback(None)
    assert result is None


def test_parse_malformed_invalid_action():
    """Callback с неизвестным action возвращает None."""
    result = parse_feedback_callback("fb:unknown:123")
    assert result is None


def test_parse_malformed_non_numeric_gen_id():
    """Callback с нечисловым gen_id игнорирует gen_id."""
    result = parse_feedback_callback("fb:like:abc")
    assert result is not None
    assert result.action == FeedbackAction.LIKE
    assert result.gen_id is None


def test_parse_malformed_only_prefix():
    """Callback только с префиксом возвращает None."""
    result = parse_feedback_callback("fb:")
    assert result is None


def test_parse_stale_gen_id_still_parsed():
    """Stale gen_id всё равно парсится (проверка совпадения в handlers)."""
    result = parse_feedback_callback("fb:like:999")
    assert result is not None
    assert result.action == FeedbackAction.LIKE
    assert result.gen_id == 999
