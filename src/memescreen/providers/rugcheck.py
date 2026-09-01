"""RugCheck -> RiskData. Public report endpoints need no key; on 401/403 the
lighter /report/summary endpoint is tried instead."""
from __future__ import annotations

from memescreen import config
from memescreen.models import HolderShare, RiskData
from memescreen.providers.base import ProviderError, request_json

_SOURCE = "rugcheck"


def _authority_enabled(*values) -> bool | None:
    """values: candidate raw fields in priority order. Non-null string -> True,
    explicit None present -> False, nothing present -> None."""
    saw_key = False
    for present, value in values:
        if not present:
            continue
        saw_key = True
        if isinstance(value, str) and value.strip():
            return True
        if value is None:
            return False
    return False if saw_key else None


def _map_report(data: dict) -> RiskData:
    token = data.get("token") or {}
    mint = _authority_enabled(
        ("mintAuthority" in data, data.get("mintAuthority")),
        ("mintAuthority" in token, token.get("mintAuthority")),
    )
    freeze = _authority_enabled(
        ("freezeAuthority" in data, data.get("freezeAuthority")),
        ("freezeAuthority" in token, token.get("freezeAuthority")),
    )

    lp_locked = None
    markets = data.get("markets") or []
    if markets:
        lp_locked = ((markets[0].get("lp") or {}).get("lpLockedPct"))
    if lp_locked is None and "lpLockedPct" in data:
        lp_locked = data.get("lpLockedPct")

    holders = []
    for h in data.get("topHolders") or []:
        if h.get("address") is not None and h.get("pct") is not None:
            holders.append(HolderShare(address=h["address"], pct=float(h["pct"])))

    flags = [r["name"] for r in (data.get("risks") or []) if r.get("name")]

    score_norm = data.get("score_normalised")
    score = int(round(score_norm)) if isinstance(score_norm, (int, float)) else None

    return RiskData(
        rugcheck_score=score,
        mint_authority_enabled=mint,
        freeze_authority_enabled=freeze,
        lp_locked_pct=float(lp_locked) if lp_locked is not None else None,
        top_holders=holders,
        risk_flags=flags,
    )


def fetch_risk_data(address: str) -> RiskData:
    report_url = f"{config.RUGCHECK_BASE}/v1/tokens/{address}/report"
    try:
        data = request_json(_SOURCE, "GET", report_url)
    except ProviderError as exc:
        if exc.reason.startswith("401") or exc.reason.startswith("403"):
            summary_url = f"{config.RUGCHECK_BASE}/v1/tokens/{address}/report/summary"
            data = request_json(_SOURCE, "GET", summary_url)
        else:
            raise
    return _map_report(data)
