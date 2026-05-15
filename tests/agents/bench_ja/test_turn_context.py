"""Unit tests for bench_ja.turn_context."""

from unittest.mock import patch

from dimos.agents.bench_ja import turn_context


def test_current_turn_is_none_by_default():
    turn_context.reset()
    assert turn_context.current_turn() is None


def test_new_turn_sets_and_returns_id():
    tid = turn_context.new_turn()
    assert isinstance(tid, str)
    assert len(tid) == 12
    assert turn_context.current_turn() == tid


def test_new_turn_replaces_previous():
    a = turn_context.new_turn()
    b = turn_context.new_turn()
    assert a != b
    assert turn_context.current_turn() == b


def test_reset_clears():
    turn_context.new_turn()
    turn_context.reset()
    assert turn_context.current_turn() is None


def test_log_bench_event_injects_kind_turn_and_t():
    turn_context.reset()
    tid = turn_context.new_turn()
    with patch.object(turn_context, "logger") as mock_logger:
        turn_context.log_bench_event("stt_done", duration_s=0.42, audio_seconds=1.5)
    assert mock_logger.info.call_count == 1
    args, kwargs = mock_logger.info.call_args
    assert kwargs["event_kind"] == "stt_done"
    assert kwargs["turn_id"] == tid
    assert isinstance(kwargs["t"], float)
    assert kwargs["duration_s"] == 0.42
    assert kwargs["audio_seconds"] == 1.5


def test_log_bench_event_without_turn():
    turn_context.reset()
    with patch.object(turn_context, "logger") as mock_logger:
        turn_context.log_bench_event("user_audio_end", audio_seconds=1.0)
    _, kwargs = mock_logger.info.call_args
    assert kwargs["turn_id"] is None


def test_new_turn_visible_from_another_thread():
    import threading

    turn_context.reset()
    tid = turn_context.new_turn()
    captured: list[str | None] = []

    def reader():
        captured.append(turn_context.current_turn())

    t = threading.Thread(target=reader)
    t.start()
    t.join()
    assert captured == [tid]
