from memescreen.journal import stats

_E = "2026-09-02T12:00:00+00:00"
_X = "2026-09-02T15:00:00+00:00"


def _t(pnl, kind="pump", size=None, entry=_E, exit=_X):
    return {"pnl_pct": pnl, "alert_kind": kind, "size_usd": size,
            "entry_at": entry, "exit_at": exit}


def test_summarize_empty():
    s = stats.summarize([])
    assert s["overall"] == {"n": 0}
    assert s["by_kind"] == {}
    assert "No closed trades yet" in stats.format_summary(s)


def test_overall_bucket_math():
    s = stats.summarize([_t(100), _t(-50), _t(20), _t(-10)])
    b = s["overall"]
    assert b["n"] == 4
    assert b["wins"] == 2 and b["losses"] == 2
    assert b["win_rate"] == 50.0
    assert b["total_pnl_pct"] == 60.0
    assert b["avg_pnl_pct"] == 15.0
    assert b["best_pnl_pct"] == 100 and b["worst_pnl_pct"] == -50


def test_split_by_alert_kind():
    s = stats.summarize([_t(100, "pump"), _t(-40, "pump"), _t(10, "spike")])
    assert set(s["by_kind"]) == {"NEW PUMP", "VOLUME SPIKE"}
    assert s["by_kind"]["NEW PUMP"]["n"] == 2
    assert s["by_kind"]["VOLUME SPIKE"]["n"] == 1


def test_net_usd_only_counts_sized_trades():
    s = stats.summarize([_t(100, size=50), _t(-50, size=100), _t(999, size=None)])
    b = s["overall"]
    # 50*1.0 + 100*(-0.5) = 50 - 50 = 0, over 2 sized trades
    assert b["net_usd"] == 0.0
    assert b["net_usd_trades"] == 2


def test_avg_hold_hours():
    s = stats.summarize([_t(10, entry="2026-09-02T12:00:00+00:00",
                            exit="2026-09-02T18:00:00+00:00")])
    assert s["overall"]["avg_hold_h"] == 6.0


def test_format_summary_contains_headline_numbers():
    s = stats.summarize([_t(100, "pump"), _t(-50, "pump")])
    text = stats.format_summary(s, open_count=1)
    assert "OVERALL" in text
    assert "NEW PUMP" in text
    assert "win rate" in text
    assert "1 position(s) still open" in text


def test_format_open():
    assert stats.format_open([]) == "No open positions."
    rows = stats.format_open([
        {"symbol": "RUN", "address": "x" * 40, "entry_price": 0.001,
         "size_usd": 50, "entry_at": "2026-09-02T12:00:00+00:00"},
    ])
    assert "RUN" in rows and "0.001" in rows and "$50" in rows
