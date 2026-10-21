"""RugCheck -> RiskData. Public report endpoints need no key; on 401/403 the
lighter /report/summary endpoint is tried instead."""
from __future__ import annotations

from memescreen import config
from memescreen.models import HolderShare, RiskData
from memescreen.providers.base import ProviderError, request_json

_SOURCE = "rugcheck"
# Below this many holders RugCheck's insider analysis hasn't run yet.
_MIN_ANALYSED_HOLDERS = 10


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


def _num(v) -> float | None:
    """A JSON number -> float. Strings, bools, None and junk -> None (a
    malformed field must never raise out of the mapper)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _insider_pct(raw_holders) -> float | None:
    """Sum of `pct` over top holders RugCheck flags `insider: true`. None when
    no holder carries the `insider` key at all (analysis absent, not zero)."""
    saw_key = False
    total = 0.0
    for h in raw_holders or []:
        if not isinstance(h, dict) or "insider" not in h:
            continue
        saw_key = True
        pct = _num(h.get("pct"))
        if h.get("insider") is True and pct is not None:
            total += pct
    return total if saw_key else None


def _insider_network_size(data: dict) -> int | None:
    nets = data.get("insiderNetworks")
    sizes = []
    if isinstance(nets, list):
        for n in nets:
            size = _num(n.get("size")) if isinstance(n, dict) else None
            if size is not None:
                sizes.append(int(size))
    if sizes:
        return max(sizes)
    detected = _num(data.get("graphInsidersDetected"))
    return int(detected) if detected is not None else None


def _creator_pct(data: dict, token: dict) -> float | None:
    balance = _num(data.get("creatorBalance"))
    supply = _num(token.get("supply"))
    if balance is None or supply is None or supply <= 0:
        return None
    return balance / supply * 100.0


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

    risks = data.get("risks") or []
    flags = [r["name"] for r in risks if r.get("name")]
    danger = [r["name"] for r in risks if r.get("name") and r.get("level") == "danger"]

    score_norm = data.get("score_normalised")
    score = int(round(score_norm)) if isinstance(score_norm, (int, float)) else None

    total_holders = _num(data.get("totalHolders"))
    insider_pct = _insider_pct(data.get("topHolders"))
    insider_net = _insider_network_size(data)
    # A minutes-old launch comes back with totalHolders 0-3 and every insider
    # flag false: RugCheck hasn't analysed it yet. Report that as unknown, not
    # as "0% insiders" -- otherwise alerts read clean and the outcome stats
    # file unanalysed tokens in the low-insider bucket. The dev share is
    # balance / supply and is real from the first block, so it stays.
    if total_holders is None or total_holders < _MIN_ANALYSED_HOLDERS:
        insider_pct = insider_net = total_holders = None

    return RiskData(
        rugcheck_score=score,
        mint_authority_enabled=mint,
        freeze_authority_enabled=freeze,
        lp_locked_pct=float(lp_locked) if lp_locked is not None else None,
        top_holders=holders,
        risk_flags=flags,
        danger_flags=danger,
        insider_pct=insider_pct,
        insider_network_size=insider_net,
        creator_pct=_creator_pct(data, token),
        total_holders=int(total_holders) if total_holders is not None else None,
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
