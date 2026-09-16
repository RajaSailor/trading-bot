import logging
from unittest.mock import MagicMock

import live_screener_main


def _reset_root_logger_handlers():
    root = logging.getLogger()
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)


def test_setup_logging_creates_log_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _reset_root_logger_handlers()

    live_screener_main.setup_logging()

    logs_dir = tmp_path / "logs"
    assert logs_dir.exists()
    log_files = list(logs_dir.glob("screener_*.log"))
    assert len(log_files) == 1

    _reset_root_logger_handlers()


def test_setup_logging_sets_expected_level_and_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    captured = {}
    file_handler = MagicMock(name="file_handler")
    stream_handler = MagicMock(name="stream_handler")

    monkeypatch.setattr(
        live_screener_main.logging, "FileHandler", lambda filename: file_handler
    )
    monkeypatch.setattr(
        live_screener_main.logging, "StreamHandler", lambda stream: stream_handler
    )
    monkeypatch.setattr(
        live_screener_main.logging,
        "basicConfig",
        lambda **kwargs: captured.update(kwargs),
    )

    live_screener_main.setup_logging()

    assert captured["level"] == logging.INFO
    assert captured["format"] == "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    assert captured["handlers"] == [file_handler, stream_handler]


def test_setup_logging_creates_logs_directory(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        live_screener_main.os,
        "makedirs",
        lambda path, exist_ok: captured.update({"path": path, "exist_ok": exist_ok}),
    )
    monkeypatch.setattr(live_screener_main.logging, "FileHandler", lambda _: MagicMock())
    monkeypatch.setattr(
        live_screener_main.logging, "StreamHandler", lambda _: MagicMock()
    )
    monkeypatch.setattr(live_screener_main.logging, "basicConfig", lambda **_: None)

    live_screener_main.setup_logging()

    assert captured == {"path": "logs", "exist_ok": True}
