"""Chart rendering.

Colours come from the validated categorical palette: fixed slot order, never
cycled, folded into "Other" past eight series. Both light and dark are selected
steps of the same eight hues, not an automatic flip.

Because a PNG has no hover layer, values have to be readable without one: single
series bars carry direct labels, lines label their endpoints, and the UI always
shows the result table beside the chart as the table-view twin.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")  # never try to open a window

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.config import settings  # noqa: E402
from app.logging_setup import get_logger  # noqa: E402

log = get_logger(__name__)

#: Categorical slots, in fixed order. Assign by position, never by rank.
SERIES_LIGHT = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
]
SERIES_DARK = [
    "#3987e5", "#d95926", "#199e70", "#c98500",
    "#d55181", "#008300", "#9085e9", "#e66767",
]

THEMES: Dict[str, Dict[str, str]] = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "baseline": "#c3c2b7",
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#ffffff",
        "secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "baseline": "#383835",
    },
}

MAX_SERIES = 8
MAX_PIE_SLICES = 6
MAX_LABEL_CHARS = 28
FONT_STACK = ["Segoe UI", "DejaVu Sans", "sans-serif"]


def series_colors(theme: str) -> List[str]:
    return SERIES_DARK if theme == "dark" else SERIES_LIGHT


def select_chart_columns(
    value_columns: Sequence[str], derive: str, chart: str
) -> tuple[List[str], str]:
    """Decide what actually goes on the axis, and what to call it.

    Counts and percentages never share an axis. When the question asked for
    growth, the growth column *is* the answer -- plotting it beside raw totals
    would put 33,100 (%) next to 5 (units) and flatten everything else.
    """
    columns = list(value_columns)

    if derive == "growth_pct" and "growth_%" in columns:
        return ["growth_%"], "growth %"

    if derive == "share_pct" and "share_%" in columns:
        # A pie already encodes share as angle; showing the raw metric is clearer.
        if chart == "pie":
            return [c for c in columns if c != "share_%"] or ["share_%"], "share of total"
        return ["share_%"], "share %"

    return columns, ""


# --- formatting -------------------------------------------------------------


def compact_number(value: float) -> str:
    """1_234_567 -> '1.23M'. Axis ticks and bar labels stay short."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "-"
    magnitude = abs(value)
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            scaled = value / threshold
            text = f"{scaled:.2f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def format_percent(value: float) -> str:
    """'33,100%' beats '33.1K' on an axis labelled "growth %"."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "-"
    # Sub-percent precision never matters for growth; "-50%" beats "-50.0%".
    if abs(value) >= 100 or float(value).is_integer():
        return f"{value:,.0f}%"
    return f"{value:,.1f}%"


def _is_percent(value_columns: Sequence[str]) -> bool:
    return bool(value_columns) and all(str(c).endswith("_%") for c in value_columns)


def _truncate(text: str) -> str:
    text = str(text)
    return text if len(text) <= MAX_LABEL_CHARS else text[: MAX_LABEL_CHARS - 1] + "…"


def _labels(frame: pd.DataFrame, label_columns: Sequence[str]) -> List[str]:
    if not label_columns:
        return [""] * len(frame)
    if len(label_columns) == 1:
        return [_truncate(v) for v in frame[label_columns[0]].astype(str)]
    joined = frame[list(label_columns)].astype(str).agg(" · ".join, axis=1)
    return [_truncate(v) for v in joined]


def _numeric(frame: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)


def chart_filename(seed: str, prefix: str = "chart") -> Path:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    directory = settings.output_dir / "charts"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{prefix}_{digest}.png"


# --- canvas -----------------------------------------------------------------


def _new_figure(theme: Dict[str, str], width: float, height: float):
    plt.rcParams["font.family"] = FONT_STACK
    figure, axes = plt.subplots(figsize=(width, height), dpi=settings.chart_dpi)
    figure.patch.set_facecolor(theme["surface"])
    axes.set_facecolor(theme["surface"])
    return figure, axes


def _style_axes(axes, theme: Dict[str, str], grid_axis: str = "y") -> None:
    """Recessive chrome: hairline solid grid, no tick marks, minimal spines."""
    if grid_axis != "none":
        axes.grid(axis=grid_axis, color=theme["grid"], linewidth=0.8, linestyle="-")
    axes.set_axisbelow(True)

    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    keep = "bottom" if grid_axis == "y" else "left"
    for side in ("bottom", "left"):
        if side == keep:
            axes.spines[side].set_color(theme["baseline"])
            axes.spines[side].set_linewidth(1.0)
        else:
            axes.spines[side].set_visible(False)

    axes.tick_params(colors=theme["muted"], labelsize=9, length=0)


#: Header geometry, in inches. Figure-fraction offsets would collapse the title
#: onto the subtitle on a short figure (a 3-bar chart is barely 3in tall).
_TITLE_INSET = 0.30
_SUBTITLE_INSET = 0.56
_HEADER_HEIGHT = 0.78


def _add_titles(figure, theme: Dict[str, str], title: str, subtitle: str) -> None:
    height = figure.get_figheight()
    if title:
        figure.suptitle(
            title, x=0.015, y=1 - _TITLE_INSET / height, ha="left", va="top",
            fontsize=13, fontweight="bold", color=theme["ink"],
        )
    if subtitle:
        figure.text(
            0.015, 1 - _SUBTITLE_INSET / height, subtitle, ha="left", va="top",
            fontsize=9.5, color=theme["secondary"],
        )


def _legend(axes, theme: Dict[str, str]) -> None:
    legend = axes.legend(
        frameon=False, fontsize=9, loc="upper left",
        bbox_to_anchor=(0, -0.12), ncol=4, handlelength=1.2, handleheight=0.9,
    )
    for text in legend.get_texts():
        text.set_color(theme["secondary"])  # text wears ink, never the series colour


def _save(figure, path: Path, theme: Dict[str, str]) -> Path:
    # Reserve a constant header band, so the plot never rides up under the title.
    figure.subplots_adjust(top=1 - _HEADER_HEIGHT / figure.get_figheight())
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        path, bbox_inches="tight", pad_inches=0.28,
        facecolor=theme["surface"], edgecolor="none",
    )
    plt.close(figure)  # closing is what keeps long sessions from leaking memory
    return path


# --- chart forms ------------------------------------------------------------


def _render_stat(value: float, theme, title, subtitle, caption, path) -> Path:
    """One number is not a bar chart -- it is a stat tile."""
    figure, axes = _new_figure(theme, 6.0, 2.6)
    axes.axis("off")
    axes.text(
        0.5, 0.56, compact_number(value), ha="center", va="center",
        fontsize=52, fontweight="bold", color=theme["ink"],
    )
    if caption:
        axes.text(0.5, 0.18, caption, ha="center", va="center",
                  fontsize=11, color=theme["secondary"])
    _add_titles(figure, theme, title, subtitle)
    return _save(figure, path, theme)


def _render_bar(frame, labels, value_columns, theme, title, subtitle,
                value_label, horizontal, path, fmt=compact_number) -> Path:
    colors = series_colors(theme_name_of(theme))
    count = len(labels)
    n_series = len(value_columns)

    if horizontal:
        height = max(2.6, 0.42 * count + 1.9)
        figure, axes = _new_figure(theme, 8.4, height)
        _style_axes(axes, theme, grid_axis="x")
    else:
        figure, axes = _new_figure(theme, max(7.0, 0.85 * count + 2.0), 4.6)
        _style_axes(axes, theme, grid_axis="y")

    positions = np.arange(count)
    # Thin marks: a full-width bar reads as a slab when there are few categories,
    # because the slot height grows as the count falls.
    group_width = 0.78 if count > 5 else (0.52 if count > 3 else 0.36)
    bar_width = group_width / max(n_series, 1)

    for index, column in enumerate(value_columns):
        values = _numeric(frame, column)
        offset = (index - (n_series - 1) / 2) * bar_width
        shared = dict(
            color=colors[index % MAX_SERIES],
            label=str(column),
            # A surface-coloured edge is the 2px gap between adjacent fills.
            edgecolor=theme["surface"],
            linewidth=1.2,
        )
        if horizontal:
            axes.barh(positions + offset, values, height=bar_width * 0.92, **shared)
        else:
            axes.bar(positions + offset, values, width=bar_width * 0.92, **shared)

    if horizontal:
        axes.set_yticks(positions)
        axes.set_yticklabels(labels)
        axes.invert_yaxis()  # largest at the top
        axes.xaxis.set_major_formatter(lambda v, _: fmt(v))
        if value_label:
            axes.set_xlabel(value_label, color=theme["secondary"], fontsize=9.5, labelpad=8)
    else:
        axes.set_xticks(positions)
        rotation = 30 if max((len(label) for label in labels), default=0) > 6 else 0
        axes.set_xticklabels(labels, rotation=rotation,
                             ha="right" if rotation else "center")
        axes.yaxis.set_major_formatter(lambda v, _: fmt(v))
        if value_label:
            axes.set_ylabel(value_label, color=theme["secondary"], fontsize=9.5, labelpad=8)

    # No tooltip exists in a PNG, so a single series carries its own values.
    if n_series == 1 and count <= 15:
        _label_bars(axes, frame, value_columns[0], positions, theme, horizontal, fmt)

    if n_series >= 2:
        _legend(axes, theme)

    _add_titles(figure, theme, title, subtitle)
    return _save(figure, path, theme)


def _label_bars(axes, frame, column, positions, theme, horizontal, fmt=compact_number) -> None:
    values = _numeric(frame, column)
    span = np.nanmax(np.abs(values)) if len(values) else 0
    pad = (span or 1) * 0.015
    for position, value in zip(positions, values, strict=True):
        if np.isnan(value):
            continue
        text = fmt(value)
        # Growth can be negative; the label belongs on the far side of the bar,
        # not on top of the zero baseline.
        offset = pad if value >= 0 else -pad
        if horizontal:
            axes.text(value + offset, position, text, va="center",
                      ha="left" if value >= 0 else "right",
                      fontsize=9, color=theme["secondary"])
        else:
            axes.text(position, value + offset, text, ha="center",
                      va="bottom" if value >= 0 else "top",
                      fontsize=9, color=theme["secondary"])


def _render_line(frame, labels, value_columns, theme, title, subtitle,
                 value_label, path, fmt=compact_number) -> Path:
    colors = series_colors(theme_name_of(theme))
    figure, axes = _new_figure(theme, 8.0, 4.4)
    _style_axes(axes, theme, grid_axis="y")

    positions = np.arange(len(labels))
    for index, column in enumerate(value_columns):
        values = _numeric(frame, column)
        color = colors[index % MAX_SERIES]
        axes.plot(
            positions, values, color=color, linewidth=1.8, marker="o",
            markersize=5, markeredgecolor=theme["surface"], markeredgewidth=1.5,
            label=str(column), zorder=3,
        )
        # Selective direct label: the endpoint only.
        if len(values) and not np.isnan(values[-1]):
            axes.annotate(
                fmt(values[-1]),
                (positions[-1], values[-1]),
                textcoords="offset points", xytext=(8, 0),
                va="center", fontsize=9, color=theme["secondary"],
            )

    axes.set_xticks(positions)
    rotation = 30 if max((len(label) for label in labels), default=0) > 6 else 0
    axes.set_xticklabels(labels, rotation=rotation, ha="right" if rotation else "center")
    axes.yaxis.set_major_formatter(lambda v, _: fmt(v))
    axes.margins(x=0.06)
    if value_label:
        axes.set_ylabel(value_label, color=theme["secondary"], fontsize=9.5, labelpad=8)

    if len(value_columns) >= 2:
        _legend(axes, theme)

    _add_titles(figure, theme, title, subtitle)
    return _save(figure, path, theme)


def _render_pie(frame, labels, column, theme, title, subtitle, path) -> Path:
    colors = series_colors(theme_name_of(theme))
    values = _numeric(frame, column)

    # Part-to-whole at a glance only: fold the tail rather than shrink slices.
    order = np.argsort(-np.nan_to_num(values))
    labels = [labels[i] for i in order]
    values = values[order]
    if len(values) > MAX_PIE_SLICES:
        tail = float(np.nansum(values[MAX_PIE_SLICES - 1 :]))
        labels = labels[: MAX_PIE_SLICES - 1] + ["Other"]
        values = np.append(values[: MAX_PIE_SLICES - 1], tail)

    figure, axes = _new_figure(theme, 7.0, 4.6)
    wedges, _, autotexts = axes.pie(
        np.nan_to_num(values),
        labels=None,
        autopct=lambda pct: f"{pct:.0f}%" if pct >= 4 else "",
        startangle=90,
        counterclock=False,
        colors=colors[: len(values)],
        wedgeprops={"edgecolor": theme["surface"], "linewidth": 2.0},
        pctdistance=0.75,
        textprops={"fontsize": 9.5},
    )
    for text in autotexts:
        text.set_color(theme["surface"] if theme is THEMES["light"] else theme["ink"])
        text.set_fontweight("bold")
    axes.axis("equal")

    legend = axes.legend(
        wedges,
        [f"{label} - {compact_number(value)}" for label, value in zip(labels, values, strict=True)],
        frameon=False, fontsize=9.5, loc="center left", bbox_to_anchor=(1.0, 0.5),
    )
    for text in legend.get_texts():
        text.set_color(theme["secondary"])

    _add_titles(figure, theme, title, subtitle)
    return _save(figure, path, theme)


def _render_scatter(frame, x_column, y_column, theme, title, subtitle, path) -> Path:
    colors = series_colors(theme_name_of(theme))
    figure, axes = _new_figure(theme, 7.2, 4.8)
    _style_axes(axes, theme, grid_axis="y")
    axes.grid(axis="x", color=theme["grid"], linewidth=0.8, linestyle="-")

    axes.scatter(
        _numeric(frame, x_column), _numeric(frame, y_column),
        s=64, color=colors[0], edgecolor=theme["surface"], linewidth=1.5, zorder=3,
    )
    axes.set_xlabel(str(x_column), color=theme["secondary"], fontsize=9.5, labelpad=8)
    axes.set_ylabel(str(y_column), color=theme["secondary"], fontsize=9.5, labelpad=8)
    axes.xaxis.set_major_formatter(lambda v, _: compact_number(v))
    axes.yaxis.set_major_formatter(lambda v, _: compact_number(v))

    _add_titles(figure, theme, title, subtitle)
    return _save(figure, path, theme)


def theme_name_of(theme: Dict[str, str]) -> str:
    return "dark" if theme is THEMES["dark"] else "light"


# --- entry point ------------------------------------------------------------


def render_chart(
    frame: pd.DataFrame,
    *,
    chart: str,
    label_columns: Sequence[str],
    value_columns: Sequence[str],
    title: str = "",
    subtitle: str = "",
    value_label: str = "",
    theme: str = "light",
    out_path: Optional[Path] = None,
) -> Optional[Path]:
    """Render `frame` and return the PNG path, or None when nothing fits."""
    if chart == "none" or frame is None or frame.empty or not len(value_columns):
        return None

    palette = THEMES.get(theme, THEMES["light"])

    # Past eight series the palette is exhausted; charting more would mean
    # cycling hues, so the extra columns stay in the table only.
    value_columns = list(value_columns)[:MAX_SERIES]

    # The seed covers everything that changes the picture -- two charts off the
    # same frame but different value columns must not share a filename.
    path = out_path or chart_filename(
        f"{title}|{chart}|{theme}|{frame.shape}|{list(label_columns)}|{value_columns}"
    )
    labels = _labels(frame, label_columns)
    fmt = format_percent if _is_percent(value_columns) else compact_number

    try:
        # One number is a stat tile, not a one-bar bar chart.
        if len(frame) == 1 and len(value_columns) == 1 and chart in ("bar", "barh", "pie"):
            value = _numeric(frame, value_columns[0])[0]
            caption = labels[0] if labels and labels[0] else value_label
            return _render_stat(value, palette, title, subtitle, caption, path)

        if chart == "line":
            return _render_line(frame, labels, value_columns, palette,
                                title, subtitle, value_label, path, fmt)

        if chart == "pie":
            # A 2-slice pie says less than two numbers do.
            if len(frame) < 3 or len(value_columns) != 1:
                chart = "bar"
            else:
                return _render_pie(frame, labels, value_columns[0], palette,
                                   title, subtitle, path)

        if chart == "scatter":
            if len(value_columns) >= 2:
                return _render_scatter(frame, value_columns[0], value_columns[1],
                                       palette, title, subtitle, path)
            chart = "bar"

        # Long category names are unreadable rotated under a vertical bar.
        longest = max((len(label) for label in labels), default=0)
        horizontal = chart == "barh" or (chart == "bar" and (longest > 14 or len(frame) > 12))
        return _render_bar(frame, labels, value_columns, palette, title, subtitle,
                           value_label, horizontal, path, fmt)

    except Exception:  # pragma: no cover - a failed chart must not kill the answer
        log.exception("Chart rendering failed")
        plt.close("all")
        return None
