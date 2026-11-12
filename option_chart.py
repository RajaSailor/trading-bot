"""Premium option lifecycle chart rendering."""

from __future__ import annotations

import io
import math
from bisect import bisect_right
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
            try:
                volume = float(raw.get("volume"))
                if not math.isfinite(volume) or volume < 0:
                    volume = None
            except (TypeError, ValueError):
                volume = None
            selected.append({"timestamp": ts, "open": open_, "high": high, "low": low,
                             "close": close, "volume": volume})
    selected = sorted({bar["timestamp"]: bar for bar in selected}.values(),
                      key=lambda candle: candle["timestamp"])
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
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
        from matplotlib.ticker import FixedLocator, FuncFormatter
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
        if not overlay.get("premium_pattern"):
            today = datetime.fromtimestamp(event_time, IST).date()
            current_session = [bar for bar in plotted
                               if datetime.fromtimestamp(bar["timestamp"], IST).date() == today]
            if current_session:
                plotted = current_session
        dates = [datetime.fromtimestamp(c["timestamp"], IST) for c in plotted]
        stamps = [bar["timestamp"] for bar in plotted]
        x = list(range(len(plotted)))
        width = .65

        def chart_x(stamp):
            stamp = _epoch(stamp)
            index = bisect_right(stamps, stamp) - 1
            if index < 0:
                return 0
            return index + min(max((stamp - stamps[index]) / interval_seconds, 0), 1)
        entry = _finite_positive(overlay.get("entry"), "entry")
        stop = _finite_positive(overlay.get("stop") or overlay.get("stop_loss"), "stop")
        target = _finite_positive(overlay.get("target"), "target")
        event_premium = overlay.get("event_premium")
        if event_premium is None:
            event_premium = overlay.get("observed_exit", overlay.get("observed_entry", entry))
        event_premium = _finite_positive(event_premium, "event_premium")

        fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
        grid = fig.add_gridspec(4, 1, hspace=.05)
        ax = fig.add_subplot(grid[:3, 0])
        volume_ax = fig.add_subplot(grid[3, 0], sharex=ax)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("#fbfbfb")
        event_x = chart_x(event_time)
        projection = max(4, len(plotted) * .12)
        right_x = event_x + projection
        opened_x = chart_x(overlay.get("opened_at") or event_time)
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
            if candle["volume"] is not None:
                volume_ax.bar(xpos, candle["volume"], width=width, color=color, alpha=.5)
        gaps = 0
        for index in range(1, len(plotted)):
            if dates[index].date() != dates[index - 1].date():
                ax.axvline(index - .5, color="#888888", linestyle=":", linewidth=.8)
                continue
            missing = (stamps[index] - stamps[index - 1]) / interval_seconds - 1
            if missing > 0:
                gaps += 1
                ax.axvline(index - .5, color="#d73027", linestyle="--", linewidth=1)
                ax.text(index - .5, .99, f"Missing {int(missing)} bars",
                        transform=ax.get_xaxis_transform(), rotation=90, va="top",
                        fontsize=7, color="#d73027")
        ax.axhline(entry, color="#1f77b4", linestyle="--", linewidth=1.2, label=f"Entry {entry:.2f}")
        stop_label = "Stop (protected gain)" if stop > entry else (
            "Stop (breakeven)" if stop == entry else "Stop")
        ax.axhline(stop, color="#2ca02c" if stop >= entry else "#d62728", linestyle="-",
                   linewidth=1.2, label=f"{stop_label} {stop:.2f}")
        ax.axhline(target, color="#2ca02c", linestyle="-", linewidth=1.2, label=f"Target {target:.2f}")
        if overlay.get("premium_support") is not None:
            support = _finite_positive(overlay["premium_support"], "premium_support")
            source = overlay.get("previous_session_date")
            ax.axhline(support, color="#9467bd", linestyle=":", linewidth=1.2,
                       label=f"Premium support {support:.2f}" + (f" ({source})" if source else ""))
        for level in overlay.get("premium_reference_levels") or []:
            price = _finite_positive(level["price"], "premium_reference_level")
            ax.axhline(price, color="#8c564b", linestyle="-.", linewidth=.9,
                       label=f"{level['label']} {price:.2f}")
        zone = overlay.get("premium_zone")
        if zone:
            low, high = (_finite_positive(zone[key], f"zone_{key}") for key in ("low", "high"))
            if low > high:
                raise ChartRenderError("invalid_premium_zone")
            ax.axhspan(low, high, color="#9467bd", alpha=.15,
                       label=f"{zone['label']} {low:.2f}-{high:.2f}")
        pattern = overlay.get("premium_pattern")
        if pattern:
            if int(pattern["timeframe"]) * 60 != interval_seconds:
                raise ChartRenderError("pattern_timeframe_mismatch")
            for vertices, label, style in [
                (pattern["points"], pattern["name"].replace("_", " "), "-o"),
                *((line["points"], line["label"], "--") for line in pattern["lines"]),
            ]:
                if not all(stamps[0] <= _epoch(p["timestamp"]) <= event_time for p in vertices):
                    raise ChartRenderError("pattern_outside_chart_window")
                ax.plot(
                    [chart_x(p["timestamp"]) for p in vertices],
                    [_finite_positive(p["price"], "pattern_price") for p in vertices],
                    style, linewidth=1.4, markersize=4, label=label,
                )
            for point in pattern["points"]:
                ax.annotate(point["label"],
                            (chart_x(point["timestamp"]),
                             _finite_positive(point["price"], "pattern_price")),
                            xytext=(0, -12), textcoords="offset points", fontsize=7)

        ref_time = overlay.get("breakout_timestamp") or overlay.get("reference_timestamp")
        ref_price = overlay.get("breakout_price") or overlay.get("reference_high")
        if ref_time and ref_price:
            try:
                ax.scatter([chart_x(ref_time)],
                           [_finite_positive(ref_price, "reference")], color="#ff7f0e", marker="^", zorder=4)
                ax.annotate("reference/breakout", xy=(chart_x(ref_time),
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
                ax.scatter([chart_x(overlay["opened_at"])],
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
            f"Data as of {as_of.strftime('%d %b %H:%M')} IST"
        )
        if pattern:
            title += f"\nConfirmed bullish premium {pattern['name'].replace('_', ' ')}"
        ax.set_title(title, fontsize=11)
        ax.set_ylabel("Option premium")
        volume_ax.set_xlabel("Trading candles (IST; closed sessions compressed)")
        volume_ax.set_ylabel("Volume", fontsize=8)
        if not any(bar["volume"] is not None for bar in plotted):
            volume_ax.text(.01, .5, "Volume unavailable", transform=volume_ax.transAxes, fontsize=8)
        ax.set_xlim(x[0] - width * 2, right_x)
        stride = max(1, len(plotted) // 8)
        volume_ax.xaxis.set_major_locator(FixedLocator(list(range(0, len(plotted), stride))))
        date_format = "%d %b\n%H:%M" if dates[0].date() != dates[-1].date() else "%H:%M"
        volume_ax.xaxis.set_major_formatter(FuncFormatter(
            lambda value, _: dates[int(value)].strftime(date_format)
            if 0 <= int(value) < len(dates) else ""))
        ax.tick_params(labelbottom=False)
        volume_ax.tick_params(labelsize=8)
        ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.5)
        ax.legend(loc="best", fontsize=8)
        lag_note = ""
        if event_time - (plotted[-1]["timestamp"] + interval_seconds) > interval_seconds:
            lag_note = " | Delayed/historical candles"
        if gaps:
            lag_note += f" | {gaps} data gaps marked; no fabricated candles"
        fig.text(0.01, 0.015, "Dhan candle data | Bot-rendered | Not a broker fill" + lag_note,
                 fontsize=8, color="#555555")
        fig.subplots_adjust(left=.07, right=.98, bottom=.14, top=.86)
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


def render_premium_chart(candles, overlay, *, width_px=1400, height_px=850, dpi=100, max_bytes=900_000) -> bytes:
    """Render a signal lifecycle premium chart as PNG bytes."""
    try:
        return _render_once(candles, overlay, width_px, height_px, dpi, max_bytes)
    except ChartRenderError as exc:
        if str(exc) != "png_too_large" or dpi <= 60:
            raise
        return _render_once(candles, overlay, width_px, height_px, 60, max_bytes)
