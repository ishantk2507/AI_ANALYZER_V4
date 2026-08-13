"""Interactive charts for the web UI.

A PNG has no hover layer, which forces compromises: labels truncated to 28
characters, values crammed beside bars, no way to inspect a mark. In a browser
none of that is necessary -- so the UI renders Vega-Lite via Altair (already a
Streamlit dependency, no new install) and keeps the PNG for the CLI and export.

Same validated palette, same rules: fixed slot order, one hue for one series,
counts and percentages never sharing an axis.
"""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd

from app.analysis.charts import MAX_PIE_SLICES, MAX_SERIES, THEMES, _is_percent, series_colors
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Vega number formats. "~s" gives 1.2M / 33k; percentages keep their sign.
_COUNT_FORMAT = "~s"
_PERCENT_FORMAT = ",.1f"

_SERIES_FIELD = "series"
_VALUE_FIELD = "value"
_LABEL_FIELD = "label"

FONT = "Segoe UI, system-ui, -apple-system, sans-serif"


def _long_form(
    frame: pd.DataFrame, label_columns: Sequence[str], value_columns: Sequence[str]
) -> pd.DataFrame:
    """One row per (label, series) so Vega can colour and group by series."""
    labels = (
        frame[list(label_columns)].astype(str).agg(" · ".join, axis=1)
        if len(label_columns) > 1
        else frame[label_columns[0]].astype(str)
        if label_columns
        else pd.Series([""] * len(frame), index=frame.index)
    )

    records = []
    for column in value_columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        for label, value in zip(labels, values, strict=True):
            records.append({_LABEL_FIELD: label, _SERIES_FIELD: str(column),
                            _VALUE_FIELD: None if pd.isna(value) else float(value)})
    return pd.DataFrame(records)


def _configure(chart, palette, title: str, subtitle: str):
    """Recessive chrome and a left-aligned header, matching the PNG renderer."""
    header = {"text": title, "anchor": "start", "color": palette["ink"],
              "fontSize": 15, "fontWeight": 600, "font": FONT, "offset": 12}
    if subtitle:
        header |= {"subtitle": subtitle, "subtitleColor": palette["secondary"],
                   "subtitleFontSize": 11, "subtitleFont": FONT}

    return (
        chart.properties(title=header, width="container")
        .configure_view(strokeWidth=0, fill=palette["surface"])
        .configure_axis(
            grid=True, gridColor=palette["grid"], gridWidth=0.8,
            domainColor=palette["baseline"], tickColor=palette["baseline"],
            labelColor=palette["muted"], titleColor=palette["secondary"],
            labelFont=FONT, titleFont=FONT, labelFontSize=11, titleFontSize=11,
            titlePadding=10,
        )
        .configure_legend(
            labelColor=palette["secondary"], titleColor=palette["secondary"],
            labelFont=FONT, titleFont=FONT, orient="bottom", direction="horizontal",
            title=None, symbolType="square", symbolSize=90,
        )
        .configure_axisY(grid=False)
        .configure(background=palette["surface"])
    )


def build_chart(
    frame: pd.DataFrame,
    *,
    chart: str,
    label_columns: Sequence[str],
    value_columns: Sequence[str],
    title: str = "",
    subtitle: str = "",
    value_label: str = "",
    theme: str = "light",
    average_line: bool = False,
    max_slices: Optional[int] = None,
):
    """Return an Altair chart, or None when there is nothing to draw.

    `average_line` draws the mean across the rows shown, and `max_slices` lifts
    the pie's fold-into-Other cap -- both for a reader who has explicitly asked
    to see the whole distribution rather than the top of it.
    """
    if chart == "none" or frame is None or frame.empty or not len(value_columns):
        return None

    try:
        import altair as alt
    except ImportError:  # pragma: no cover - altair ships with streamlit
        return None

    palette = THEMES.get(theme, THEMES["light"])
    colors = series_colors(theme)
    value_columns = list(value_columns)[:MAX_SERIES]
    label_columns = list(label_columns)

    percent = _is_percent(value_columns)
    number_format = _PERCENT_FORMAT if percent else _COUNT_FORMAT
    axis_title = value_label or (", ".join(value_columns))

    data = _long_form(frame, label_columns, value_columns)
    if data.empty:
        return None

    single = len(value_columns) == 1
    label_title = " · ".join(label_columns) if label_columns else None

    tooltip = [
        alt.Tooltip(f"{_LABEL_FIELD}:N", title=label_title or "item"),
        alt.Tooltip(f"{_VALUE_FIELD}:Q", title=axis_title, format=",.2f"),
    ]
    if not single:
        tooltip.insert(1, alt.Tooltip(f"{_SERIES_FIELD}:N", title="series"))

    color = (
        alt.value(colors[0])
        if single
        else alt.Color(f"{_SERIES_FIELD}:N", title=None,
                       scale=alt.Scale(domain=value_columns, range=colors[: len(value_columns)]))
    )

    try:
        if chart == "line":
            built = _line(alt, data, color, tooltip, axis_title, number_format,
                          label_title, palette, single, colors)
        elif chart == "pie" and single:
            built = _pie(alt, data, colors, palette, axis_title, max_slices)
        elif chart == "scatter" and len(value_columns) >= 2:
            built = _scatter(alt, frame, value_columns, label_columns, colors, palette)
        else:
            built = _bar(alt, data, color, tooltip, axis_title, number_format,
                         label_title, single, value_columns)
            if average_line:
                built = built + _average_rule(alt, data, palette, number_format)
        return _configure(built, palette, title, subtitle)
    except Exception:  # pragma: no cover - a chart must never break the answer
        log.exception("Interactive chart failed")
        return None


#: Pixels per bar, and the slot each sits in. Fixed rather than a fraction of
#: the chart height, or two bars in a tall chart render as slabs.
BAR_THICKNESS = 20
BAR_SLOT = 30


def _bar(alt, data, color, tooltip, axis_title, number_format, label_title,
         single, value_columns):
    """Horizontal bars: category names stay readable without truncation."""
    rows = data[_LABEL_FIELD].nunique()
    series = 1 if single else len(value_columns)
    thickness = BAR_THICKNESS if single else max(9, BAR_THICKNESS // series + 2)
    height = max(110, min(BAR_SLOT * rows * series + 60, 900))

    encoding = dict(
        y=alt.Y(f"{_LABEL_FIELD}:N", title=label_title, sort="-x",
                axis=alt.Axis(labelLimit=320)),
        x=alt.X(f"{_VALUE_FIELD}:Q", title=axis_title,
                axis=alt.Axis(format=number_format)),
        color=color,
        tooltip=tooltip,
    )
    if not single:
        encoding["yOffset"] = alt.YOffset(f"{_SERIES_FIELD}:N", sort=value_columns)

    return alt.Chart(data).mark_bar(
        cornerRadiusTopRight=3, cornerRadiusBottomRight=3, size=thickness,
    ).encode(**encoding).properties(height=height)


def _average_rule(alt, data, palette, number_format):
    """The mean across the rows shown, so a bar can be read against its peers."""
    mean = float(data[_VALUE_FIELD].mean(skipna=True))
    reference = pd.DataFrame([{_VALUE_FIELD: mean}])
    rule = alt.Chart(reference).mark_rule(
        color=palette["secondary"], strokeWidth=1.5, strokeDash=[4, 3],
    ).encode(
        x=alt.X(f"{_VALUE_FIELD}:Q"),
        tooltip=[alt.Tooltip(f"{_VALUE_FIELD}:Q", title="average", format=",.2f")],
    )
    label = alt.Chart(reference).mark_text(
        align="left", dx=4, dy=-6, fontSize=10, color=palette["secondary"], font=FONT,
    ).encode(x=alt.X(f"{_VALUE_FIELD}:Q"), text=alt.value("avg"))
    return rule + label


def _line(alt, data, color, tooltip, axis_title, number_format, label_title,
          palette, single, colors):
    """Line with a crosshair: the hover layer a time series needs."""
    base = alt.Chart(data).encode(
        x=alt.X(f"{_LABEL_FIELD}:N", title=label_title, sort=None),
        y=alt.Y(f"{_VALUE_FIELD}:Q", title=axis_title,
                axis=alt.Axis(format=number_format)),
        color=color,
    )
    hover = alt.selection_point(fields=[_LABEL_FIELD], nearest=True,
                                on="mouseover", empty=False)

    line = base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(
        size=70, filled=True, stroke=palette["surface"], strokeWidth=1.5))

    # The selection is declared once, on the invisible hit layer; the rule only
    # filters against it. Declaring it twice makes Altair dedupe and warn.
    points = base.mark_point(size=200, filled=True, opacity=0).encode(
        tooltip=tooltip).add_params(hover)
    rule = (
        alt.Chart(data)
        .mark_rule(color=palette["baseline"], strokeWidth=1)
        .encode(x=alt.X(f"{_LABEL_FIELD}:N", sort=None))
        .transform_filter(hover)
    )

    return (line + rule + points).properties(height=340)


def _pie(alt, data, colors, palette, axis_title, max_slices=None):
    """Part-to-whole, tail folded rather than shrunk into slivers.

    The fold is lifted when the reader asked for the whole distribution. Past
    the eight-hue palette the extra slices repeat colours, so identity comes
    from the legend and the tooltip rather than from hue alone.
    """
    limit = max(int(max_slices), 2) if max_slices else MAX_PIE_SLICES

    ordered = data.dropna(subset=[_VALUE_FIELD]).sort_values(_VALUE_FIELD, ascending=False)
    if len(ordered) > limit:
        head = ordered.head(limit - 1)
        tail = float(ordered.iloc[limit - 1 :][_VALUE_FIELD].sum())
        ordered = pd.concat([head, pd.DataFrame(
            [{_LABEL_FIELD: "Other", _SERIES_FIELD: "Other", _VALUE_FIELD: tail}])])

    names = ordered[_LABEL_FIELD].tolist()
    colors = (colors * (len(names) // len(colors) + 1))[: len(names)]
    return alt.Chart(ordered).mark_arc(
        innerRadius=62, stroke=palette["surface"], strokeWidth=2,
    ).encode(
        theta=alt.Theta(f"{_VALUE_FIELD}:Q", stack=True),
        color=alt.Color(f"{_LABEL_FIELD}:N", title=None, sort=names,
                        scale=alt.Scale(domain=names, range=colors[: len(names)])),
        order=alt.Order(f"{_VALUE_FIELD}:Q", sort="descending"),
        tooltip=[alt.Tooltip(f"{_LABEL_FIELD}:N", title="item"),
                 alt.Tooltip(f"{_VALUE_FIELD}:Q", title=axis_title, format=",.2f")],
    ).properties(height=320)


def _scatter(alt, frame, value_columns, label_columns, colors, palette):
    x_column, y_column = value_columns[0], value_columns[1]
    tooltip = [alt.Tooltip(f"{c}:N") for c in label_columns] + [
        alt.Tooltip(f"{x_column}:Q", format=",.2f"),
        alt.Tooltip(f"{y_column}:Q", format=",.2f"),
    ]
    return alt.Chart(frame).mark_circle(
        size=110, color=colors[0], stroke=palette["surface"], strokeWidth=1.5, opacity=0.9,
    ).encode(
        x=alt.X(f"{x_column}:Q", axis=alt.Axis(format=_COUNT_FORMAT)),
        y=alt.Y(f"{y_column}:Q", axis=alt.Axis(format=_COUNT_FORMAT)),
        tooltip=tooltip,
    ).properties(height=360)
