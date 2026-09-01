import pytest

from memescreen import cli
from memescreen.models import TokenReport
from datetime import UTC, datetime


def _fake_report(address):
    r = TokenReport(
        address=address, fetched_at=datetime.now(UTC), market=None, risk=None,
        holders=None, provider_errors={},
    )
    r.flags = []
    r.score = 50
    r.verdict = "CAUTION"
    return r


VALID = "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"


def test_screen_happy_path(monkeypatch, capsys):
    monkeypatch.setattr(cli.orchestrator, "screen", lambda a: _fake_report(a))
    rc = cli.main(["screen", VALID])
    out = capsys.readouterr().out
    assert rc == 0
    assert "CAUTION" in out
    assert VALID in out


def test_screen_prints_report_exactly_once(monkeypatch, capsys):
    monkeypatch.setattr(cli.orchestrator, "screen", lambda a: _fake_report(a))
    rc = cli.main(["screen", VALID])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("VERDICT") == 1


def test_screen_rejects_bad_address(capsys):
    rc = cli.main(["screen", "not-an-address!!!"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "address" in err.lower()


def test_scan_is_not_available_yet(capsys):
    rc = cli.main(["scan"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Plan 2" in err


def test_no_subcommand_returns_2(capsys):
    rc = cli.main([])
    assert rc == 2
