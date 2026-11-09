"""Shared components for the Streamlit app (docs/DESIGN_SYSTEM.md).

Every value inserted into HTML passes through ``html.escape`` (database values, model output and user input
alike); ``st.html`` also sanitises and runs no JavaScript. Charts are Altair (bundled with Streamlit), so no
extra charting dependency is needed. Every chart sits beside a table of the same figures.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence

import altair as alt
import pandas as pd
import streamlit as st

from portfolioiq.formatting import MINUS, NOT_AVAILABLE, pct
from portfolioiq.timing import format_ms

# Tokens. The Method A / Method B pair passes the colour-vision validator on white (docs/DESIGN_SYSTEM.md).
PAGE, SURFACE, SUNKEN, RULE, ROW_RULE = "#F6F7F9", "#FFFFFF", "#EEF1F5", "#D9DEE6", "#E7EBF0"
INK, INK_2, INK_MUTED = "#14202E", "#44505F", "#5C6877"
NAVY, ACCENT, ACCENT_INK, ACCENT_TINT = "#0F1E33", "#1F5FBF", "#174A96", "#E6EEF9"
METHOD_A, METHOD_B = "#1F5FBF", "#C2702A"
GREEN, AMBER, RED = "#1B7A44", "#8A5A00", "#B3261E"
EQUITY, BONDS, UNCLASSIFIED = METHOD_A, METHOD_B, "#5B6FB5"
LIGHT_BLUE, DARK_BLUE = "#8DB2E8", "#123C7C"  # the two ends of a dumbbell: one hue, light to dark
EXCLUDED, RESIDUAL, GRID, AXIS = "#8A94A3", "#B8C0CC", "#E7EBF0", "#C5CCD6"
FONT = "IBM Plex Sans"

PAGES: dict = {}  # filled by streamlit_app.py: page key -> st.Page, for cross-page links

CSS = f"""
<style>
[data-testid="stMainBlockContainer"], .block-container {{ max-width: 1240px; padding-top: 2.25rem; }}
.piq-context {{ font-size: 12.5px; line-height: 18px; color: {INK_MUTED}; margin: 0 0 14px 0; padding-bottom: 8px;
  border-bottom: 1px solid {RULE}; font-variant-numeric: tabular-nums; }}
.piq-context b {{ color: {INK}; font-weight: 600; }}
.piq-section {{ font-size: 12px; line-height: 16px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  color: {INK_2}; margin: 0 0 4px 0; }}
.piq-title {{ font-size: 28px; line-height: 34px; font-weight: 600; color: {NAVY}; margin: 0 0 4px 0; letter-spacing: -.01em; }}
.piq-subtitle {{ font-size: 16px; line-height: 22px; font-weight: 600; color: {ACCENT_INK}; margin: 0 0 8px 0; }}
.piq-purpose {{ font-size: 15px; line-height: 23px; color: {INK_2}; margin: 0 0 14px 0; max-width: 860px; }}
.piq-stat {{ font-size: 14px; line-height: 22px; color: {INK}; margin: 0 0 14px 0; font-variant-numeric: tabular-nums; }}
.piq-stat .sep {{ color: {INK_MUTED}; padding: 0 6px; }}
.piq-head {{ border-top: 1px solid {RULE}; padding-top: 10px; margin-top: 18px; }}
.piq-head-title {{ font-size: 16px; line-height: 24px; font-weight: 600; color: {NAVY}; margin: 0; }}
.piq-head-sub {{ font-size: 13.5px; line-height: 20px; color: {INK_2}; margin: 2px 0 8px 0; }}
.piq-foot {{ font-size: 12.5px; line-height: 18px; color: {INK_MUTED}; margin: 8px 0 0 0; overflow-wrap: anywhere; }}
.piq-foot span + span::before {{ content: " · "; }}
.piq-table-wrap {{ overflow-x: auto; border: 1px solid {RULE}; border-radius: 4px; }}
table.piq-table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; line-height: 20px; color: {INK};
  font-variant-numeric: tabular-nums; background: {SURFACE}; }}
table.piq-table th {{ background: {SUNKEN}; color: {INK_2}; font-size: 11.5px; line-height: 16px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .04em; text-align: left; padding: 8px 10px; border-bottom: 1px solid {RULE};
  vertical-align: bottom; }}
table.piq-table td {{ padding: 7px 10px; border-bottom: 1px solid {ROW_RULE}; vertical-align: top; }}
table.piq-table tr:last-child td {{ border-bottom: none; }}
table.piq-table .num {{ text-align: right; white-space: nowrap; }}
table.piq-table tr.total td {{ font-weight: 600; border-top: 1px solid {INK_MUTED}; }}
table.piq-table tr:hover td {{ background: #F3F6FA; }}
.piq-chip {{ display: inline-block; font-size: 11.5px; line-height: 16px; font-weight: 600; padding: 2px 6px;
  border-radius: 3px; white-space: nowrap; letter-spacing: .02em; }}
.piq-chip--PASS {{ color: {GREEN}; background: #E3F3E9; }}
.piq-chip--WARNING {{ color: {AMBER}; background: #FDF1D8; }}
.piq-chip--FAIL {{ color: {RED}; background: #FBE4E2; }}
.piq-chip--NA, .piq-chip--PENDING {{ color: {INK_2}; background: {SUNKEN}; }}
.piq-chip--REFUSED {{ color: {ACCENT_INK}; background: {ACCENT_TINT}; }}
.piq-stamp {{ border-left: 3px solid {ACCENT}; background: {SUNKEN}; padding: 8px 12px; border-radius: 3px; margin: 6px 0 10px 0; }}
.piq-stamp .label {{ font-size: 12px; line-height: 16px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; }}
.piq-stamp .reason {{ font-size: 13px; line-height: 18px; color: {INK_2}; margin-top: 2px; }}
.piq-stamp--INDEPENDENTLY_RECONCILED, .piq-stamp--RESULT_VALIDATED {{ background: #E3F3E9; border-left-color: {GREEN}; }}
.piq-stamp--INDEPENDENTLY_RECONCILED .label, .piq-stamp--RESULT_VALIDATED .label {{ color: #145C33; }}
.piq-stamp--STRUCTURE_VALIDATED {{ border-left-color: {INK_2}; }}
.piq-stamp--EXECUTED {{ border-left-color: {RULE}; }}
.piq-stamp--VERIFICATION_FAILED {{ border-left-color: {RED}; background: #FBE4E2; }}
.piq-stamp--VERIFICATION_FAILED .label {{ color: {RED}; }}
.piq-stamp--VERIFICATION_UNAVAILABLE {{ border-left-color: {RULE}; }}
.piq-stamp--VERIFICATION_UNAVAILABLE .label {{ color: {INK_2}; }}
.piq-callout {{ border-left: 3px solid {INK_2}; padding: 9px 13px; border-radius: 3px; margin: 8px 0 12px 0;
  font-size: 13.5px; line-height: 20px; color: {INK}; background: {SUNKEN}; }}
.piq-callout b {{ font-weight: 600; }}
.piq-callout--warning {{ border-left-color: {AMBER}; background: #FDF1D8; }}
.piq-callout--error {{ border-left-color: {RED}; background: #FBE4E2; }}
.piq-callout--demo {{ border-left-color: {ACCENT}; background: {ACCENT_TINT}; }}
.piq-callout--ok {{ border-left-color: {GREEN}; background: #E3F3E9; }}
.piq-empty {{ border: 1px dashed {RULE}; background: {SURFACE}; padding: 16px 18px; border-radius: 4px; margin: 8px 0; }}
.piq-empty .t {{ font-weight: 600; color: {INK}; margin-bottom: 4px; }}
.piq-empty .d {{ color: {INK_2}; font-size: 13.5px; }}
.piq-empty code {{ font-family: 'IBM Plex Mono', monospace; font-size: 12.5px; background: {SUNKEN}; padding: 1px 4px; }}
.piq-answer {{ font-size: 16px; line-height: 25px; color: {INK}; margin: 6px 0 12px 0; }}
.piq-segbar {{ display: flex; height: 12px; border-radius: 3px; overflow: hidden; background: {SUNKEN}; margin: 6px 0 6px 0; }}
.piq-segbar div {{ height: 100%; border-right: 2px solid {SURFACE}; }}
.piq-segbar div:last-child {{ border-right: none; }}
.piq-legend {{ font-size: 13px; color: {INK_2}; font-variant-numeric: tabular-nums; }}
.piq-legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin: 0 5px 0 0; vertical-align: -1px; }}
.piq-legend span {{ margin-right: 14px; white-space: nowrap; display: inline-block; }}
.piq-kv {{ font-size: 13.5px; line-height: 21px; border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }}
.piq-kv td {{ padding: 3px 0; border-bottom: 1px solid {ROW_RULE}; vertical-align: top; overflow-wrap: anywhere; }}
.piq-kv td:first-child {{ color: {INK_2}; width: 36%; padding-right: 10px; }}
.piq-mono {{ font-family: 'IBM Plex Mono', monospace; font-size: 12.5px; }}
.piq-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; margin: 6px 0 4px 0; }}
.piq-card {{ background: {SURFACE}; border: 1px solid {RULE}; border-radius: 4px; padding: 12px 14px; }}
.piq-card .h {{ font-weight: 600; color: {NAVY}; font-size: 14.5px; line-height: 20px; margin-bottom: 4px; }}
.piq-card .b {{ color: {INK_2}; font-size: 13.5px; line-height: 20px; }}
.piq-metrics {{ display: flex; flex-wrap: wrap; gap: 1px; background: {RULE};
  border: 1px solid {RULE}; border-radius: 4px; overflow: hidden; margin: 6px 0 10px 0; }}
.piq-metric {{ flex: 1 1 128px; background: {SURFACE}; padding: 9px 12px; }}
.piq-metric .l {{ font-size: 11.5px; line-height: 15px; color: {INK_MUTED}; letter-spacing: .02em; }}
.piq-metric .v {{ font-size: 19px; line-height: 26px; font-weight: 600; color: {NAVY}; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }}
.piq-metric .v.small {{ font-size: 14px; line-height: 26px; }}
.piq-metric .n {{ font-size: 11.5px; line-height: 15px; color: {INK_2}; }}
.piq-flow {{ display: flex; flex-wrap: wrap; align-items: stretch; gap: 4px; margin: 6px 0 4px 0; }}
.piq-flow .step {{ flex: 1 1 130px; background: {SURFACE}; border: 1px solid {RULE}; border-radius: 4px; padding: 8px 10px; }}
.piq-flow .step .t {{ font-weight: 600; font-size: 13px; line-height: 18px; color: {NAVY}; }}
.piq-flow .step .d {{ font-size: 12px; line-height: 16px; color: {INK_2}; margin-top: 2px; }}
.piq-flow .arrow {{ align-self: center; color: {INK_MUTED}; font-size: 14px; flex: 0 0 auto; }}
.piq-path {{ display: flex; flex-wrap: wrap; gap: 4px; margin: 2px 0 8px 0; }}
.piq-path span {{ font-size: 12px; line-height: 16px; padding: 3px 8px; border: 1px solid {RULE}; border-radius: 3px; color: {INK_MUTED}; background: {SURFACE}; }}
.piq-path span.on {{ background: {NAVY}; border-color: {NAVY}; color: #FFFFFF; font-weight: 600; }}
.piq-steps {{ font-size: 13px; line-height: 20px; color: {INK_2}; margin: 0 0 8px 0; font-variant-numeric: tabular-nums; }}
.piq-steps b {{ color: {INK}; font-weight: 600; }}
.piq-steps .sep {{ color: {INK_MUTED}; padding: 0 5px; }}
.piq-formula {{ font-family: 'IBM Plex Mono', monospace; font-size: 12.5px; line-height: 19px; background: {SUNKEN};
  padding: 6px 9px; border-radius: 3px; margin: 4px 0 2px 0; overflow-wrap: anywhere; }}
[data-testid="stVegaLiteChart"] {{ overflow-x: auto; }}
@media (max-width: 640px) {{ .piq-title {{ font-size: 24px; line-height: 30px; }} .piq-flow .arrow {{ display: none; }} }}
</style>
"""


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def inject_css() -> None:
    st.html(CSS)


# --- page furniture --------------------------------------------------------------------------------

def context_strip(as_of: str | None, fingerprint: str, model: str, mode: str) -> None:
    st.html(f'<div class="piq-context">Data as of <b>{esc(as_of or NOT_AVAILABLE)}</b> · dataset fingerprint '
            f'<span class="piq-mono">{esc(fingerprint[:12])}</span> · model {esc(model)} · {esc(mode)}</div>')


def page_header(title: str, purpose: str, subtitle: str | None = None) -> None:
    sub = f'<div class="piq-subtitle">{esc(subtitle)}</div>' if subtitle else ""
    st.html(f'<div class="piq-title">{esc(title)}</div>{sub}<div class="piq-purpose">{esc(purpose)}</div>')


def head(title: str, subtitle: str | None = None) -> None:
    sub = f'<div class="piq-head-sub">{esc(subtitle)}</div>' if subtitle else ""
    st.html(f'<div class="piq-head"><div class="piq-head-title">{esc(title)}</div>{sub}</div>')


def section_label(text: str) -> None:
    st.html(f'<div class="piq-section" style="margin-top:10px">{esc(text)}</div>')


def stat_line(items: Iterable[str]) -> None:
    parts = '<span class="sep">·</span>'.join(esc(i) for i in items)
    st.html(f'<div class="piq-stat">{parts}</div>')


def footnote(*parts: str) -> None:
    st.html('<div class="piq-foot">' + "".join(f"<span>{esc(p)}</span>" for p in parts if p) + "</div>")


def cards(items: Sequence[tuple[str, str]]) -> None:
    """Plain bordered panels: (heading, body)."""
    body = "".join(f'<div class="piq-card"><div class="h">{esc(h)}</div><div class="b">{esc(b)}</div></div>' for h, b in items)
    st.html(f'<div class="piq-grid">{body}</div>')


def metrics(items: Sequence[tuple[str, object, str | None]], small: bool = False) -> None:
    """A compact strip of labelled figures: (label, value, note)."""
    cls = "v small" if small else "v"
    cells = "".join(f'<div class="piq-metric"><div class="l">{esc(l)}</div><div class="{cls}">{esc(v)}</div>'
                    + (f'<div class="n">{esc(n)}</div>' if n else "") + "</div>" for l, v, n in items)
    st.html(f'<div class="piq-metrics">{cells}</div>')


def flow(steps_: Sequence[tuple[str, str]]) -> None:
    """A left-to-right process diagram that wraps at narrow widths."""
    arrow = '<div class="arrow" aria-hidden="true">→</div>'
    st.html('<div class="piq-flow">' + arrow.join(
        f'<div class="step"><div class="t">{esc(t)}</div><div class="d">{esc(d)}</div></div>' for t, d in steps_) + "</div>")


def path(options: Sequence[str], active: str | None) -> None:
    """Which of the possible workflows ran. The active one is filled and named in text, never shown by colour alone."""
    st.html('<div class="piq-path">' + "".join(
        f'<span class="on">Selected: {esc(o)}</span>' if o == active else f"<span>{esc(o)}</span>" for o in options) + "</div>")


def steps(items: Sequence[str]) -> None:
    """Ordered execution steps. Items are trusted markup: the caller builds them with ``esc``."""
    sep = '<span class="sep">→</span>'
    st.html('<div class="piq-steps">' + sep.join(items) + "</div>")


def formula(text: str) -> None:
    st.html(f'<div class="piq-formula">{esc(text)}</div>')


def go(page_key: str) -> None:
    """Open another page (does nothing when one view is rendered on its own, as in tests)."""
    page = PAGES.get(page_key)
    if page is not None:
        st.switch_page(page)


def link(page_key: str, label: str) -> None:
    page = PAGES.get(page_key)
    if page is not None:
        st.page_link(page, label=label)


def chip(status: str) -> str:
    """HTML for a status chip. Statuses always carry their text label, never colour alone."""
    key = {"NOT_APPLICABLE": "NA", "N/A": "NA", "NOT EVALUATED": "PENDING"}.get(status, status)
    label = {"NOT_APPLICABLE": "N/A"}.get(status, status)
    return f'<span class="piq-chip piq-chip--{esc(key)}">{esc(label)}</span>'


STAMP_TEXT = {
    "INDEPENDENTLY_RECONCILED": "Independently reconciled",
    "RESULT_VALIDATED": "Result validated",
    "NOT_INDEPENDENTLY_VERIFIABLE": "Not independently verifiable",
    "STRUCTURE_VALIDATED": "Structure validated",
    "EXECUTED": "Executed",
    "VERIFICATION_FAILED": "Verification failed",
    "VERIFICATION_UNAVAILABLE": "Verification unavailable",
}


def stamp(state: str, reason: str) -> None:
    st.html(f'<div class="piq-stamp piq-stamp--{esc(state)}"><div class="label">{esc(STAMP_TEXT.get(state, state))}'
            f'</div><div class="reason">{esc(reason)}</div></div>')


def callout(kind: str, label: str, text: str) -> None:
    st.html(f'<div class="piq-callout piq-callout--{esc(kind)}"><b>{esc(label)}.</b> {esc(text)}</div>')


def empty_state(title: str, text: str, command: str | None = None) -> None:
    cmd = f' <code>{esc(command)}</code>' if command else ""
    st.html(f'<div class="piq-empty"><div class="t">{esc(title)}</div><div class="d">{esc(text)}{cmd}</div></div>')


def key_values(rows: Sequence[tuple[str, object]]) -> None:
    body = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in rows)
    st.html(f'<table class="piq-kv">{body}</table>')


def table(columns: Sequence[str], rows: Sequence[Sequence[object]], numeric: set[int] = frozenset(),
          html_columns: set[int] = frozenset(), total: Sequence[object] | None = None,
          widths: Sequence[str] | None = None) -> None:
    """A table. ``html_columns`` hold trusted markup built by this module (chips); all else is escaped.
    ``widths`` optionally fixes column widths (CSS values, one per column)."""
    def cell(i: int, value: object) -> str:
        content = str(value) if i in html_columns else esc(value)
        return f'<td class="num">{content}</td>' if i in numeric else f"<td>{content}</td>"

    def width(i: int) -> str:
        return f' style="width:{esc(widths[i])}"' if widths and i < len(widths) else ""

    head_ = "".join(f'<th class="num"{width(i)}>{esc(c)}</th>' if i in numeric else f"<th{width(i)}>{esc(c)}</th>"
                    for i, c in enumerate(columns))
    body = "".join("<tr>" + "".join(cell(i, v) for i, v in enumerate(r)) + "</tr>" for r in rows)
    if total is not None:
        body += '<tr class="total">' + "".join(cell(i, v) for i, v in enumerate(total)) + "</tr>"
    st.html(f'<div class="piq-table-wrap"><table class="piq-table"><thead><tr>{head_}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def short(text: object, limit: int = 220) -> str:
    """Long messages cut to one readable cell; the full text stays in the report file."""
    value = " ".join(str(text).split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def segmented_bar(parts: Sequence[tuple[str, float, str]]) -> None:
    """A thin bar of shares that sum to 1 (equity, bonds, residual), with a labelled legend."""
    shown = [(label, max(share, 0.0), colour) for label, share, colour in parts]
    bar = "".join(f'<div style="width:{share * 100:.4f}%;background:{colour}" title="{esc(label)} {pct(share)}%"></div>'
                  for label, share, colour in shown if share > 0)
    legend = "".join(f'<span><i style="background:{colour}"></i>{esc(label)} {pct(share)}%</span>'
                     for label, share, colour in parts)
    st.html(f'<div class="piq-segbar">{bar}</div><div class="piq-legend">{legend}</div>')


def verdict_bar(counts: Sequence[tuple[str, int, str]]) -> None:
    """A thin composition bar for verdict counts, with each count named in the legend."""
    total = sum(n for _, n, _ in counts) or 1
    bar = "".join(f'<div style="width:{n / total * 100:.2f}%;background:{c}" title="{esc(l)} {n}"></div>'
                  for l, n, c in counts if n)
    legend = "".join(f'<span><i style="background:{c}"></i>{esc(l)} {n}</span>' for l, n, c in counts)
    st.html(f'<div class="piq-segbar">{bar}</div><div class="piq-legend">{legend}</div>')


# --- formatting --------------------------------------------------------------------------------------

def money(value: float | None, decimals: int = 0) -> str:
    if value is None:
        return NOT_AVAILABLE
    text = f"{abs(value):,.{decimals}f}"
    return f"{MINUS}{text}" if value < 0 else text


def millions(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value / 1e6:,.1f}"


def percent(fraction: float | None) -> str:
    return pct(fraction)


def ms(value: float | None) -> str:
    """A measured duration (the backend's own formatter); a stage that was not measured shows n/a, never zero."""
    return format_ms(value)


def cell(value) -> str:
    """One SQL result value for display: integers and floats grouped, nothing rounded beyond six decimals."""
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{value:,.6f}".rstrip("0").rstrip(".") if not value.is_integer() else f"{value:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


# --- charts (Altair) ---------------------------------------------------------------------------------

def _style(chart: alt.Chart) -> alt.Chart:
    # Fit the width but keep room for long axis labels on the left and direct value labels on the right.
    chart = chart.properties(autosize=alt.AutoSizeParams(type="fit", contains="padding"),
                             padding={"left": 8, "right": 44, "top": 6, "bottom": 6})
    return (chart.configure(background=SURFACE, font=FONT)
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=INK_MUTED, titleColor=INK_2, labelFontSize=12, titleFontSize=12,
                            titleFontWeight="normal", gridColor=GRID, domainColor=AXIS, tickColor=AXIS,
                            labelFont=FONT, titleFont=FONT)
            .configure_legend(labelColor=INK_2, titleColor=INK_2, labelFont=FONT, titleFont=FONT, orient="top",
                              direction="horizontal", symbolType="square", labelLimit=320)
            .configure_text(font=FONT, color=INK_2))


def show(chart: alt.Chart) -> None:
    st.altair_chart(_style(chart), width="stretch", theme=None)


def ranked_bars(data: pd.DataFrame, label: str, value: str, *, value_title: str, colour: str | None = None,
                fmt: str = ".2f", domain_max: float | None = None) -> alt.Chart:
    """Ranked horizontal bars, largest first; direct labels when there are at most 8 bars.

    Streamlit fits the whole chart (axes and legend included) into the height, so it allows for both.
    """
    order = list(data[label])
    height = 28 * len(data) + 50
    x = alt.X(f"{value}:Q", title=value_title, scale=alt.Scale(domain=[0, domain_max]) if domain_max else alt.Undefined,
              axis=alt.Axis(grid=True, tickCount=5))
    y = alt.Y(f"{label}:N", sort=order, title=None, axis=alt.Axis(labelLimit=260, ticks=False, domain=False))
    tooltip = [alt.Tooltip(f"{label}:N", title=label), alt.Tooltip(f"{value}:Q", title=value_title, format=fmt)]
    base = alt.Chart(data).encode(y=y)
    bars = base.mark_bar(cornerRadiusEnd=4, height=16).encode(x=x, color=alt.value(colour or ACCENT), tooltip=tooltip)
    if len(data) <= 8:
        text = base.mark_text(align="left", dx=4, fontSize=12, color=INK_2).encode(
            x=f"{value}:Q", text=alt.Text(f"{value}:Q", format=fmt))
        return (bars + text).properties(height=height)
    return bars.properties(height=height)


def grouped_method_bars(data: pd.DataFrame, label: str, *, value_title: str) -> alt.Chart:
    """Method A and Method B side by side for each sector: the same figures as the table beside it."""
    order = list(dict.fromkeys(data[label]))
    methods = ["Method A (% of portfolio)", "Method B (% of equity sleeve)"]
    return alt.Chart(data).mark_bar(cornerRadiusEnd=3, height=11).encode(
        y=alt.Y(f"{label}:N", sort=order, title=None, axis=alt.Axis(labelLimit=260, ticks=False, domain=False)),
        yOffset=alt.YOffset("Method:N", sort=methods),
        x=alt.X("Share:Q", title=value_title, scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(grid=True, tickCount=5)),
        color=alt.Color("Method:N", scale=alt.Scale(domain=methods, range=[METHOD_A, METHOD_B]),
                        legend=alt.Legend(title=None)),
        tooltip=[alt.Tooltip(f"{label}:N", title="Sector"), alt.Tooltip("Method:N", title="Method"),
                 alt.Tooltip("Share:Q", title="Share (%)", format=".2f")],
    ).properties(height=34 * len(order) + 84)


def stacked_share_bars(data: pd.DataFrame, label: str, segment: str, value: str, colours: dict[str, str],
                       order: list[str]) -> alt.Chart:
    """100% stacked horizontal bars (asset mix), segments in a fixed order and colour per entity."""
    height = 26 * data[label].nunique() + 78  # rows plus the axis and the legend
    seg_order = {s: i for i, s in enumerate(colours)}
    data = data.assign(_order=data[segment].map(seg_order))
    return alt.Chart(data).mark_bar(height=14, stroke=SURFACE, strokeWidth=2).encode(
        y=alt.Y(f"{label}:N", sort=order, title=None, axis=alt.Axis(labelLimit=260, ticks=False, domain=False)),
        x=alt.X(f"{value}:Q", stack="zero", title="Share of portfolio weight (%)", scale=alt.Scale(domain=[0, 100]),
                axis=alt.Axis(grid=True, tickCount=5)),
        color=alt.Color(f"{segment}:N", scale=alt.Scale(domain=list(colours), range=list(colours.values())),
                        legend=alt.Legend(title=None)),
        order=alt.Order("_order:Q"),
        tooltip=[alt.Tooltip(f"{label}:N", title="Portfolio"), alt.Tooltip(f"{segment}:N", title="Part"),
                 alt.Tooltip(f"{value}:Q", title="Share (%)", format=".2f")],
    ).properties(height=height)


def dumbbell(data: pd.DataFrame, label: str, start: str, end: str, *, start_name: str, end_name: str,
             value_title: str, start_colour: str = LIGHT_BLUE, end_colour: str = DARK_BLUE) -> alt.Chart:
    """One row per item: a rule from start to end, a light dot for start and a dark dot for end."""
    order = list(data[label])
    long = data.melt(id_vars=[label], value_vars=[start, end], var_name="measure", value_name="value")
    long["measure"] = long["measure"].map({start: start_name, end: end_name})
    height = 30 * len(data) + 80  # rows plus the axis and the legend
    y = alt.Y(f"{label}:N", sort=order, title=None, axis=alt.Axis(labelLimit=260, ticks=False, domain=False))
    rule = alt.Chart(data).mark_rule(color=AXIS, strokeWidth=2).encode(
        y=y, x=alt.X(f"{start}:Q", title=value_title, axis=alt.Axis(grid=True, tickCount=5)), x2=f"{end}:Q")
    dots = alt.Chart(long).mark_circle(size=90, opacity=1, stroke=SURFACE, strokeWidth=2).encode(
        y=y, x="value:Q",
        color=alt.Color("measure:N", scale=alt.Scale(domain=[start_name, end_name], range=[start_colour, end_colour]),
                        legend=alt.Legend(title=None)),
        tooltip=[alt.Tooltip(f"{label}:N", title=label), alt.Tooltip("measure:N", title="Measure"),
                 alt.Tooltip("value:Q", title=value_title, format=".2f")])
    return (rule + dots).properties(height=height)


def dot_plot_reference(data: pd.DataFrame, label: str, value: str, flag: str, *, reference: float,
                       value_title: str) -> alt.Chart:
    """Dots against a solid reference line; off-target dots in the warning colour with a text label."""
    order = list(data[label])
    height = 26 * len(data) + 56
    y = alt.Y(f"{label}:N", sort=order, title=None, axis=alt.Axis(labelLimit=260, ticks=False, domain=False))
    lo, hi = float(data[value].min()), float(data[value].max())
    span = max(abs(reference - lo), abs(hi - reference), 1.0) * 1.6
    scale = alt.Scale(domain=[reference - span, reference + span], zero=False)  # shared by every layer
    x = alt.X(f"{value}:Q", title=value_title, scale=scale, axis=alt.Axis(grid=True, tickCount=7))
    ref = alt.Chart(pd.DataFrame({"r": [reference]})).mark_rule(color=INK_2, strokeWidth=1.5).encode(
        x=alt.X("r:Q", scale=scale))
    dots = alt.Chart(data).mark_circle(size=80, opacity=1, stroke=SURFACE, strokeWidth=2).encode(
        y=y, x=x, color=alt.condition(f"datum.{flag}", alt.value(AMBER), alt.value(ACCENT)),
        tooltip=[alt.Tooltip(f"{label}:N", title="Portfolio"), alt.Tooltip(f"{value}:Q", title=value_title, format=".2f")])
    labels = alt.Chart(data).transform_filter(f"datum.{flag}").mark_text(align="left", dx=8, fontSize=12, color=AMBER).encode(
        y=y, x=alt.X(f"{value}:Q", scale=scale), text=alt.Text(f"{value}:Q", format=".2f"))
    return (ref + dots + labels).properties(height=height)


def strip(data: pd.DataFrame, value: str, label: str, *, value_title: str, colour: str = ACCENT) -> alt.Chart:
    """A strip chart of individual observations (latency), one mark per case."""
    return alt.Chart(data).mark_tick(thickness=2, size=22, color=colour).encode(
        x=alt.X(f"{value}:Q", title=value_title, axis=alt.Axis(grid=True, tickCount=6)),
        tooltip=[alt.Tooltip(f"{label}:N", title="Case"), alt.Tooltip(f"{value}:Q", title=value_title, format=",.1f")],
    ).properties(height=96)
