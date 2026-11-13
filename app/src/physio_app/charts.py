"""Shared Plotly styling. Colors are the validated dark-surface palette; slots in fixed order."""

import pandas as pd
import plotly.graph_objects as go

SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
MUTED = "#8b8a84"
GRID = "rgba(255,255,255,0.07)"
SIGNAL = {"ecg": "#199e70", "ppg": "#e66767"}


def style(fig: go.Figure, height: int = 320, legend: bool = True, unified: bool = True):
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=36 if legend else 12, b=8),
        hovermode="x unified" if unified else "closest",
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title=None),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        bargap=0.25,
        font=dict(size=12),
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, ticks="outside", tickcolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    return fig


def line(fig: go.Figure, x, y, name: str, color: str, *, dash=None, markers=True, **kw):
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            name=name,
            mode="lines+markers" if markers else "lines",
            line=dict(color=color, width=2, dash=dash),
            marker=dict(size=8, color=color, line=dict(width=2, color="#1a1a19")),
            connectgaps=False,
            **kw,
        )
    )
    return fig


def band(fig: go.Figure, x, low, high, name: str):
    """Shaded range between two series (e.g. a baseline band)."""
    fig.add_trace(
        go.Scatter(
            x=x, y=high, mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=low,
            mode="lines",
            line=dict(width=0),
            fill="tonexty",
            fillcolor="rgba(139,138,132,0.18)",
            name=name,
            hoverinfo="skip",
        )
    )
    return fig


def tag_markers(fig: go.Figure, journal: pd.DataFrame):
    """Dotted vertical lines on days with journal tags; hover shows the tags."""
    if journal.empty:
        return fig
    for _, r in journal.iterrows():
        text = ", ".join(r["tags"]) if len(r["tags"]) else "note"
        fig.add_vline(x=pd.Timestamp(r["day"]), line=dict(color=MUTED, width=1, dash="dot"))
        fig.add_annotation(
            x=pd.Timestamp(r["day"]),
            y=1,
            yref="paper",
            text="◆",
            showarrow=False,
            font=dict(color=MUTED, size=10),
            hovertext=f"{text}<br>{r['note']}",
        )
    return fig
