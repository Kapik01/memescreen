import json
from pathlib import Path

import httpx
import pytest
import respx

from memescreen.providers.base import ProviderError
from memescreen.providers.rugcheck import fetch_risk_data

FIX = Path(__file__).parent.parent / "fixtures"
ADDR = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"
REPORT_URL = f"https://api.rugcheck.xyz/v1/tokens/{ADDR}/report"
SUMMARY_URL = f"https://api.rugcheck.xyz/v1/tokens/{ADDR}/report/summary"


def _load(name):
    return json.loads((FIX / name).read_text())


@respx.mock
def test_full_report_mapping():
    respx.get(REPORT_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report.json"))
    )
    r = fetch_risk_data(ADDR)
    assert r.mint_authority_enabled is False
    assert r.freeze_authority_enabled is False
    assert r.lp_locked_pct == 100.0
    assert r.rugcheck_score == 12
    assert [h.pct for h in r.top_holders] == [4.2, 3.1, 2.0]
    assert "Low amount of LP Providers" in r.risk_flags


@respx.mock
def test_danger_level_risk_is_captured_separately():
    respx.get(REPORT_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report_danger.json"))
    )
    r = fetch_risk_data(ADDR)
    assert r.danger_flags == ["Rugged"]
    # danger entries stay in risk_flags too (spec §4 shape is unchanged)
    assert r.risk_flags == ["Rugged", "Low amount of LP Providers"]
    # score is below the danger cutoff, so only the level can drive RED
    assert r.rugcheck_score == 45


@respx.mock
def test_warn_only_report_has_no_danger_flags():
    respx.get(REPORT_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_report.json"))
    )
    assert fetch_risk_data(ADDR).danger_flags == []


@respx.mock
def test_mint_authority_present_means_enabled():
    data = _load("rugcheck_report.json")
    data["mintAuthority"] = "SomeAuthorityPubkey"
    respx.get(REPORT_URL).mock(return_value=httpx.Response(200, json=data))
    r = fetch_risk_data(ADDR)
    assert r.mint_authority_enabled is True


@respx.mock
def test_falls_back_to_summary_on_401():
    respx.get(REPORT_URL).mock(return_value=httpx.Response(401))
    respx.get(SUMMARY_URL).mock(
        return_value=httpx.Response(200, json=_load("rugcheck_summary.json"))
    )
    r = fetch_risk_data(ADDR)
    assert r.lp_locked_pct == 100.0
    assert r.rugcheck_score == 12
    assert r.top_holders == []
    assert r.mint_authority_enabled is None


@respx.mock
def test_non_auth_error_propagates():
    respx.get(REPORT_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(ProviderError):
        fetch_risk_data(ADDR)
