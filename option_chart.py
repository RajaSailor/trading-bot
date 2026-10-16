"""Premium option lifecycle chart rendering."""

from __future__ import annotations

import io
import math
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class ChartUnavailable(RuntimeError):
    """Raised when chart dependencies are unavailable."""


class ChartRenderError(RuntimeError):
    """Raised when a chart cannot be rendered safely."""


def _finite_positive(value, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} boolean")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} invalid")
    return number


def _epoch(value) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=IST)
        return value.timestamp()
    number = _finite_positive(value, "timestamp")
    return number / 1000 if number > 1e11 else number


def select_chart_candles(candles, event_time, *, interval_seconds=600, window=80):
    """Return validated completed candles visible at ``event_time``."""
    event_epoch = _epoch(event_time)
    interval_seconds = max(1, int(interval_seconds))
    window = max(20, min(150, int(window)))
    selected = []
    for raw in candles or []:
        try:
            ts = _epoch(raw["timestamp"])
            open_ = _finite_positive(raw["open"], "open")
            high = _finite_positive(raw["high"], "high")
            low = _finite_positive(raw["low"], "low")
            close = _finite_positive(raw["close"], "close")
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if high < max(open_, close) or low > min(open_, close):
            continue
        if ts + interval_seconds <= event_epoch:
            selected.append({"timestamp": ts, "open": open_, "high": high, "low": low, "close": close})
    selected.sort(key=lambda candle: candle["timestamp"])
    if not selected:
        raise ChartRenderError("no_completed_candles")
    return selected[-window:]


def _label_for_event(event: str) -> str:
    return {
        "OPEN": "POSITION OPEN TRIGGER",
        "STOP": "STOP LOSS TRIGGERED",
        "TARGET": "TARGET ACHIEVED",
        "SESSION_CLOSE": "SESSION CLOSE EXIT",
    }.get(str(event or "").upper(), str(event or "EVENT"))


def _render_once(candles, overlay, width_px, height_px, dpi, max_bytes):
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except Exception as exc:  # pragma: no cover - environment dependent
        raise ChartUnavailable("matplotlib_unavailable") from exc

    fig = None
    buffer = None
    try:
        event_time = _epoch(overlay.get("event_time") or overlay.get("opened_at"))
        interval_seconds = int(overlay.get("interval_seconds") or overlay.get("interval_minutes", 10) * 60)
        plotted = select_chart_candles(
            candles,
            event_time,
            interval_seconds=interval_seconds,
            window=overlay.get("window", 80),
        )
        dates = [datetime.fromtimestamp(c["timestamp"], IST) for c in plotted]
        x = mdates.date2num(dates)
        width = (interval_seconds / 86400) * 0.65
        entry = _finite_positive(overlay.get("entry"), "entry")
        stop = _finite_positive(overlay.get("stop") or overlay.get("stop_loss"), "stop")
        target = _finite_positive(overlay.get("target"), "target")
        event_premium = overlay.get("event_premium")
        if event_premium is None:
            event_premium = overlay.get("observed_exit", overlay.get("observed_entry", entry))
        event_premium = _finite_positive(event_premium, "event_premium")

        fig, ax = plt.subplots(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("#fbfbfb")
        event_x = mdates.date2num(datetime.fromtimestamp(event_time, IST))
        projection = max(interval_seconds / 86400 * 4, (event_x - x[0]) * .25)
        right_x = event_x + projection
        opened_x = mdates.date2num(datetime.fromtimestamp(
            _epoch(overlay.get("opened_at") or event_time), IST))
        shade_start = max(min(opened_x, event_x), x[0])
        ax.add_patch(Rectangle((shade_start, entry), right_x - shade_start, target - entry,
                               facecolor="#2ca02c", alpha=0.10, edgecolor="none"))
        if stop < entry:
            ax.add_patch(Rectangle((shade_start, stop), right_x - shade_start, entry - stop,
                                   facecolor="#d62728", alpha=0.10, edgecolor="none"))
        ax.text(event_x + projection / 2, .02, "Level projection\n(no future candles)",
                transform=ax.get_xaxis_transform(), ha="center", fontsize=7, color="#666666")
        for xpos, candle in zip(x, plotted):
            color = "#1a9850" if candle["close"] >= candle["open"] else "#d73027"
            ax.vlines(xpos, candle["low"], candle["high"], color=color, linewidth=1.1)
            lower = min(candle["open"], candle["close"])
            body = abs(candle["close"] - candle["open"])
            if body == 0:
                ax.hlines(candle["close"], xpos - width / 2, xpos + width / 2, color=color, linewidth=1.5)
            else:
                ax.add_patch(Rectangle((xpos - width / 2, lower), width, body,
                                       facecolor=color, edgecolor=color, alpha=0.85))
        ax.axhline(entry, color="#1f77b4", linestyle="--", linewidth=1.2, label=f"Entry {entry:.2f}")
        ax.axhline(stop, color="#d62728", linestyle="-", linewidth=1.2, label=f"Stop {stop:.2f}")
        ax.axhline(target, color="#2ca02c", linestyle="-", linewidth=1.2, label=f"Target {target:.2f}")

        ref_time = overlay.get("breakout_timestamp") or overlay.get("reference_timestamp")
        ref_price = overlay.get("breakout_price") or overlay.get("reference_high")
        if ref_time and ref_price:
            try:
                ax.scatter([mdates.date2num(datetime.fromtimestamp(_epoch(ref_time), IST))],
                           [_finite_positive(ref_price, "reference")], color="#ff7f0e", marker="^", zorder=4)
                ax.annotate("reference/breakout", xy=(mdates.date2num(datetime.fromtimestamp(_epoch(ref_time), IST)),
                                                       _finite_positive(ref_price, "reference")),
                            xytext=(5, 10), textcoords="offset points", fontsize=8, color="#ff7f0e")
            except (TypeError, ValueError, OverflowError, OSError):
                pass

        event_label = _label_for_event(str(overlay.get("event") or ""))
        ax.scatter([event_x], [event_premium], color="#111111", s=36, zorder=5)
        ax.annotate(f"{event_label}\n{event_premium:.2f}", xy=(event_x, event_premium),
                    xytext=(8, 12), textcoords="offset points", fontsize=8,
                    bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "#333333", "alpha": 0.85})
        if str(overlay.get("event") or "").upper() != "OPEN" and overlay.get("opened_at"):
            try:
                ax.scatter([mdates.date2num(datetime.fromtimestamp(_epoch(overlay["opened_at"]), IST))],
                           [_finite_positive(overlay.get("observed_entry", entry), "observed_entry")],
                           color="#1f77b4", s=25, zorder=4)
            except (TypeError, ValueError, OverflowError, OSError):
                pass

        as_of = datetime.fromtimestamp(plotted[-1]["timestamp"] + interval_seconds, IST)
        contract = overlay.get("option_symbol") or overlay.get("contract") or "Option contract"
        title = (
            f"{overlay.get('underlying', '')} {contract} | {overlay.get('option_type', '')} "
            f"{overlay.get('strike', '')} | Exp {overlay.get('expiry', '')}\n"
            f"{overlay.get('timeframe', overlay.get('interval_minutes', 10))} | {event_label} | "
            f"Data as of {as_of.strftime('%H:%M')} IST"
        )
        ax.set_title(title, fontsize=11)
        ax.set_ylabel("Option premium")
        ax.set_xlabel("Time (IST)")
        ax.set_xlim(x[0] - width * 2, right_x)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=IST))
        ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.5)
        ax.legend(loc="best", fontsize=8)
        lag_note = ""
        if event_time - (plotted[-1]["timestamp"] + interval_seconds) > interval_seconds:
            lag_note = " | Delayed/historical candles"
        fig.text(0.01, 0.015, "Signal-only / hypothetical — not a broker fill" + lag_note,
                 fontsize=8, color="#555555")
        fig.tight_layout(rect=(0, 0.035, 1, 1))
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=dpi)
        data = buffer.getvalue()
        if len(data) > max_bytes:
            raise ChartRenderError("png_too_large")
        return data
    finally:
        if fig is not None:
            plt.close(fig)
        if buffer is not None:
            buffer.close()


def render_premium_chart(candles, overlay, *, width_px=1000, height_px=600, dpi=100, max_bytes=900_000) -> bytes:
    """Render a signal lifecycle premium chart as PNG bytes."""
    try:
        return _render_once(candles, overlay, width_px, height_px, dpi, max_bytes)
    except ChartRenderError as exc:
        if str(exc) != "png_too_large" or dpi <= 60:
            raise
        return _render_once(candles, overlay, width_px, height_px, 60, max_bytes)
