"""TokenReport -> terminal text. Uses rich for layout but returns a plain
string so it is trivially testable and pipe-friendly."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from memescreen.models import TokenReport

_ICON = {"red": "🔴", "yellow": "🟡", "green": "🟢", "unknown": "⚪"}


def _fmt_usd(value: float | None) -> str | None:
    return f"${value:,.0f}" if value is not None else None


def _age(report: TokenReport) -> str | None:
    if not report.market or report.market.pair_created_at is None:
        return None
    minutes = (report.fetched_at - report.market.pair_created_at).total_seconds() / 60
    if minutes < 60:
        return f"{minutes:.0f} min"
    if minutes < 60 * 48:
        return f"{minutes / 60:.1f} h"
    return f"{minutes / 60 / 24:.1f} d"


def render_report(report: TokenReport) -> str:
    console = Console(record=True, width=100)
    m = report.market

    symbol = (m.symbol if m and m.symbol else None) or report.address
    name = f"  ({m.name})" if m and m.name else ""
    console.print(f"[bold]{symbol}[/bold]{name}")
    console.print(
        f"VERDICT: {report.verdict}  ·  score {report.score}/100"
    )
    console.print("")

    table = Table(show_header=False, box=None, pad_edge=False)
    for f in report.flags:
        table.add_row(_ICON.get(f.level, "?"), f.check, "—", f.message)
    console.print(table)
    console.print("")

    market_lines = [
        ("Price", f"${m.price_usd:.8f}".rstrip("0") if m and m.price_usd is not None else None),
        ("Liquidity", _fmt_usd(m.liquidity_usd) if m and m.liquidity_usd is not None else None),
        ("24h volume", _fmt_usd(m.volume_24h_usd) if m and m.volume_24h_usd is not None else None),
        ("Market cap", _fmt_usd(m.market_cap_usd) if m and m.market_cap_usd is not None else None),
        ("Age", _age(report)),
        ("DEX", m.dex if m and m.dex is not None else None),
    ]
    printed_any = False
    for label, value in market_lines:
        if value is not None:
            console.print(f"{label:<12} {value}")
            printed_any = True
    if m and m.socials:
        console.print(f"{'Socials':<12} " + "  ".join(m.socials))
        printed_any = True
    if printed_any:
        console.print("")

    if report.provider_errors:
        console.print("[bold]Data gaps[/bold]")
        for source, reason in report.provider_errors.items():
            console.print(f"  {source}: {reason}")
        console.print("")

    console.print(f"Contract: {report.address}")
    console.print(f"Chart:    https://dexscreener.com/solana/{report.address}")

    return console.export_text()
