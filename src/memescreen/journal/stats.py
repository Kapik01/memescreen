"""Pure P&L math over a list of closed-trade dicts (as `JournalStore`
returns them). No I/O -- `summarize` -> dict, `format_summary` -> the
plain-text block the bot posts."""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any, Iterable

_KINDS = {"spike": "VOLUME SPIKE", "pump": "NEW PUMP", None: "untagged"}


def _hold_hours(t: dict[str, Any]) -> float | None:
    try:
        a = datetime.fromisoformat(t["entry_at"])
        b = datetime.fromisoformat(t["exit_at"])
    except (TypeError, ValueError):
        return None
    return (b - a).total_seconds() / 3600.0


def _bucket(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [t["pnl_pct"] for t in trades if t.get("pnl_pct") is not None]
    n = len(pnls)
    if n == 0:
        return {"n": 0}
    wins = sum(1 for p in pnls if p > 0)
    holds = [h for h in (_hold_hours(t) for t in trades) if h is not None]
    # Net USD only from trades that carried a size, so a partial-coverage
    # bucket does not silently understate.
    sized = [t for t in trades if t.get("size_usd") and t.get("pnl_pct") is not None]
    net_usd = sum(t["size_usd"] * t["pnl_pct"] / 100.0 for t in sized) if sized else None
    return {
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": wins / n * 100.0,
        "total_pnl_pct": sum(pnls),
        "avg_pnl_pct": statistics.fmean(pnls),
        "median_pnl_pct": statistics.median(pnls),
        "best_pnl_pct": max(pnls),
        "worst_pnl_pct": min(pnls),
        "avg_hold_h": statistics.fmean(holds) if holds else None,
        "net_usd": net_usd,
        "net_usd_trades": len(sized),
    }


def summarize(closed: Iterable[dict[str, Any]]) -> dict[str, Any]:
    trades = list(closed)
    by_kind: dict[str, dict[str, Any]] = {}
    for key in ("spike", "pump", None):
        sub = [t for t in trades if (t.get("alert_kind") or None) == key]
        if sub:
            by_kind[_KINDS[key]] = _bucket(sub)
    return {"overall": _bucket(trades), "by_kind": by_kind}


def _fmt_bucket(label: str, b: dict[str, Any]) -> list[str]:
    if b.get("n", 0) == 0:
        return [f"{label}: no closed trades yet"]
    lines = [
        f"{label}  ({b['n']} trades)",
        f"  win rate    {b['win_rate']:.0f}%   ({b['wins']}W / {b['losses']}L)",
        f"  avg P&L     {b['avg_pnl_pct']:+.1f}%   (median {b['median_pnl_pct']:+.1f}%)",
        f"  total P&L   {b['total_pnl_pct']:+.1f}%",
        f"  best/worst  {b['best_pnl_pct']:+.0f}% / {b['worst_pnl_pct']:+.0f}%",
    ]
    if b.get("avg_hold_h") is not None:
        lines.append(f"  avg hold    {b['avg_hold_h']:.1f}h")
    if b.get("net_usd") is not None:
        lines.append(f"  net USD     ${b['net_usd']:+,.0f}   (over {b['net_usd_trades']} sized)")
    return lines


def format_summary(s: dict[str, Any], open_count: int = 0) -> str:
    overall = s["overall"]
    if overall.get("n", 0) == 0:
        base = "No closed trades yet. Reply to an alert with `in <price>`, then `out <price>` when you sell."
        return base if not open_count else f"{base}\n({open_count} position(s) still open.)"
    out = ["=== trade journal ===", *_fmt_bucket("OVERALL", overall)]
    for label, b in s["by_kind"].items():
        out += ["", *_fmt_bucket(label, b)]
    if open_count:
        out += ["", f"{open_count} position(s) still open (see !open)."]
    return "\n".join(out)


def format_open(positions: list[dict[str, Any]]) -> str:
    if not positions:
        return "No open positions."
    rows = ["open positions:"]
    for p in positions:
        tag = p.get("symbol") or p["address"][:8]
        size = f"  ${p['size_usd']:g}" if p.get("size_usd") else ""
        rows.append(f"  {tag}  in @ {p['entry_price']:g}{size}   ({p['entry_at'][:16]}Z)")
    return "\n".join(rows)
