import json

import pytest

from alert_disclaimer import PUBLIC_DISCLAIMER
from lifecycle_charts import LifecycleChartService
from price_action_confirmations import CONFIRMATION_NAMES
from signal_lifecycle import SignalLifecycle
from telegram_handler import format_option_breakout_alert
from tests.test_index_swing_strategy import NOW, incoming
from tests.test_signal_lifecycle import payload


@pytest.mark.parametrize("category,underlying", [
    ("index_options", "NIFTY"), ("nifty50_stock_options", "RELIANCE"),
    ("commodity_options", "GOLD"),
])
@pytest.mark.parametrize("event", ["OPEN", "MODIFY", "STOP", "TARGET", "SESSION_CLOSE"])
def test_each_category_and_lifecycle_event_contains_full_one_line_notice(
        tmp_path, category, underlying, event):
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: NOW)
    position = service.open_once(payload(underlying=underlying, category=category))
    assert position is not None
    position.update(
        closed_at=NOW.timestamp(), previous_stop_loss=position["stop_loss"],
        previous_target=position["target"], exit_premium=position["entry"] + 1,
        observed_exit=position["entry"] + 1,
    )
    message = service._signal_message(position, event)
    assert message.endswith(PUBLIC_DISCLAIMER)
    assert message.count(PUBLIC_DISCLAIMER) == 1
    assert "\n" not in PUBLIC_DISCLAIMER
    alert = {**position["metadata"], "category": category}
    assert format_option_breakout_alert(alert).endswith(PUBLIC_DISCLAIMER)


@pytest.mark.parametrize("category", [
    "index_options", "commodity_options", "nifty50_stock_options",
])
def test_each_report_part_has_disclaimer_without_exceeding_message_budget(tmp_path, category):
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: NOW)
    with service._transaction() as db:
        service._enqueue_report(db, "report-test", category, "DAILY SIGNAL REPORT\n",
                                [f"Symbol {number}: " + "x" * 650 for number in range(12)])
        rows = db.execute("SELECT message FROM lifecycle_outbox ORDER BY sequence").fetchall()
    assert len(rows) > 1
    assert all(row["message"].endswith(PUBLIC_DISCLAIMER) for row in rows)
    assert all(len(row["message"]) <= 3500 for row in rows)
    assert sum("Hypothetical signal results" in row["message"] for row in rows) == 1


def test_index_chart_caption_and_serialized_outbox_preserve_notice_and_evidence(tmp_path):
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: NOW)
    position = service.open_once(incoming())
    with service._connection() as db:
        row = db.execute("SELECT message,position FROM lifecycle_outbox").fetchone()
    stored = json.loads(row["position"])
    assert stored["metadata"]["price_action_confirmations"]
    assert stored["metadata"]["gap_context"]["direction"] == "gap_down"
    assert row["message"].endswith(PUBLIC_DISCLAIMER)
    # Even all four evidence labels stay within an ordinary photo caption.
    for kind in CONFIRMATION_NAMES:
        position["metadata"]["price_action_confirmations"].append({"kind": kind})
    message = service._signal_message(position, "OPEN")
    assert len(message.encode("utf-16-le")) // 2 <= 1024
    assert message.count("Liquidity sweep and reclaim") == 1
    charts = LifecycleChartService(
        tmp_path / "charts.db", lambda *a, **k: True, lambda *a, **k: [], enabled=True)
    assert charts._caption(position).endswith(PUBLIC_DISCLAIMER)
    long_caption = charts._caption({"option_symbol": "\U0001f4c8" * 1500})
    assert len(long_caption.encode("utf-16-le")) // 2 <= 1024
    assert long_caption.endswith(PUBLIC_DISCLAIMER)


def test_missing_pnl_terminal_message_still_has_notice(tmp_path):
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: NOW)
    position = service.open_once(incoming())
    position.update(closed_at=NOW.timestamp(), exit_premium=None)
    position.pop("lot_size")
    message = service._signal_message(position, "SESSION_CLOSE")
    assert "P&amp;L: N/A" in message
    assert message.endswith(PUBLIC_DISCLAIMER)
