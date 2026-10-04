from datetime import datetime, timedelta, timezone

import pytest

from trader import controls
from trader.brokers.paper import PaperBroker
from trader.config import Config
from trader.journal import Journal
from trader.models import Account, Position, Proposal
from trader.reporting import build_report, max_drawdown_pct
from trader.risk import RiskGuard

PB, LB = "mean-reversion", "momentum-quality"
NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)  # a Monday


@pytest.fixture
def env(tmp_path):
    cfg = Config(db_path=str(tmp_path / "t.db"), allowed_account_id="ACC1")
    j = Journal(cfg.db_path)
    return cfg, j, RiskGuard(cfg, j)


def acct(equity=100_000, cash=100_000, positions=(), aid="ACC1"):
    return Account(aid, equity, cash, list(positions))


def buy(sym="AAPL", qty=10, **kw):
    return Proposal(sym, "buy", qty, **kw)


def started(j, mode=PB):
    controls.start(j, mode, 30, NOW.date())


def test_stopped_by_default(env):
    cfg, j, g = env
    d = g.check(PB, buy(), acct(), 100, NOW)
    assert not d.approved and "stopped" in d.reasons[0]


def test_approves_and_caps_position_at_10pct(env):
    cfg, j, g = env
    started(j)
    d = g.check(PB, buy(qty=500), acct(), 100, NOW)
    assert d.approved and d.qty == 100  # 10% of 100k / $100


def test_existing_position_counts_toward_cap(env):
    cfg, j, g = env
    started(j)
    a = acct(equity=100_000, cash=95_000, positions=[Position("AAPL", 50, 100)])
    d = g.check(PB, buy(qty=500), a, 100, NOW)
    assert d.qty == 50


def test_invested_cap(env):
    cfg, j, g = env
    started(j)
    pos = [Position(s, 80, 100) for s in "ABCDEFGH"]  # 8 x 8k = 64k
    a = acct(100_000, 36_000, pos)
    pos2 = pos + [Position("I", 100, 100), Position("J", 100, 100)]  # +20k = 84k invested
    a = acct(100_000, 16_000, pos2)
    d = g.check(PB, buy("Z", 100), a, 100, NOW)
    assert d.qty == 10  # only 1k of room under 85%


def test_equities_only_and_symbol_checks(env):
    cfg, j, g = env
    started(j)
    assert not g.check(PB, buy(asset_type="option"), acct(), 100, NOW).approved
    assert not g.check(PB, buy("BTC-USD"), acct(), 100, NOW).approved
    assert not g.check(PB, buy(), acct(), 0, NOW).approved


def test_limit_price_sanity(env):
    cfg, j, g = env
    started(j)
    d = g.check(PB, buy(order_type="limit", limit_price=120), acct(), 100, NOW)
    assert not d.approved


def test_live_requires_account_lock(env):
    cfg, j, g = env
    started(j, LB)
    assert not g.check(LB, buy(), acct(aid="OTHER"), 100, NOW).approved
    assert g.check(LB, buy(), acct(aid="ACC1"), 100, NOW).approved


def test_live_blocked_when_account_unconfigured(tmp_path):
    cfg = Config(db_path=str(tmp_path / "t.db"))
    j = Journal(cfg.db_path)
    started(j, LB)
    d = RiskGuard(cfg, j).check(LB, buy(), acct(), 100, NOW)
    assert not d.approved and "not configured" in d.reasons[0]


def test_daily_loss_blocks_buys_but_allows_sells(env):
    cfg, j, g = env
    started(j)
    g.refresh(PB, acct(100_000), NOW)
    a = acct(96_500, 50_000, [Position("AAPL", 100, 100)])  # -3.5%
    later = NOW + timedelta(minutes=10)
    assert not g.check(PB, buy("MSFT"), a, 100, later).approved
    sell = Proposal("AAPL", "sell", 100)
    assert g.check(PB, sell, a, 100, later).approved


def test_drawdown_auto_pauses(env):
    cfg, j, g = env
    started(j)
    g.refresh(PB, acct(100_000), NOW)
    g.refresh(PB, acct(87_000), NOW + timedelta(days=1))
    assert controls.get_state(j, PB) == controls.PAUSED
    d = g.check(PB, buy(), acct(87_000, 87_000), 100, NOW + timedelta(days=1))
    assert not d.approved and "paused" in d.reasons[0]


def test_drawdown_warning_only(env):
    cfg, j, g = env
    started(j)
    g.refresh(PB, acct(100_000), NOW)
    g.refresh(PB, acct(93_000), NOW + timedelta(days=1))
    assert controls.get_state(j, PB) == controls.RUNNING
    assert j.db.execute("SELECT COUNT(*) c FROM events WHERE kind='warning'").fetchone()["c"] == 1


def test_kill_file_blocks_everything(env, tmp_path):
    cfg, j, g = env
    started(j)
    controls.stop_file(cfg.db_path).write_text("x")
    a = acct(positions=[Position("AAPL", 10, 100)])
    assert not g.check(PB, buy(), a, 100, NOW).approved
    assert not g.check(PB, Proposal("AAPL", "sell", 10), a, 100, NOW).approved


def test_pause_allows_exits_only(env):
    cfg, j, g = env
    started(j)
    controls.set_state(j, PB, controls.PAUSED, "manual")
    a = acct(positions=[Position("AAPL", 10, 100)])
    assert not g.check(PB, buy("MSFT"), a, 100, NOW).approved
    assert g.check(PB, Proposal("AAPL", "sell", 10), a, 100, NOW).approved


def test_window_end_blocks_buys(env):
    cfg, j, g = env
    started(j)
    late = NOW + timedelta(days=60)
    assert not g.check(PB, buy(), acct(), 100, late).approved


def test_no_shorting_and_sell_clamped(env):
    cfg, j, g = env
    started(j)
    assert not g.check(PB, Proposal("AAPL", "sell", 5), acct(), 100, NOW).approved
    a = acct(positions=[Position("AAPL", 3, 100)])
    d = g.check(PB, Proposal("AAPL", "sell", 5), a, 100, NOW + timedelta(seconds=1))
    assert d.approved and d.qty == 3


def test_duplicate_and_order_limit(env):
    cfg, j, g = env
    started(j)
    assert g.check(PB, buy(), acct(), 100, NOW).approved
    assert not g.check(PB, buy(), acct(), 100, NOW + timedelta(seconds=30)).approved
    cfg2 = Config(db_path=cfg.db_path, max_orders_per_day=1)
    g2 = RiskGuard(cfg2, j)
    assert not g2.check(PB, buy("MSFT"), acct(), 100, NOW + timedelta(minutes=1)).approved


def test_paper_roundtrip_and_report(env):
    cfg, j, g = env
    started(j)
    b = PaperBroker(cfg, j, PB)
    g.check(PB, buy(qty=10), b.account(), 100, NOW)
    b.fill("AAPL", "buy", 10, 100)
    b.mark({"AAPL": 110})
    g.refresh(PB, b.account(), NOW + timedelta(hours=1))
    b.fill("AAPL", "sell", 10, 110)
    g.refresh(PB, b.account(), NOW + timedelta(hours=2))
    rep = build_report(j, PB)
    assert rep["closed_lots"] == 1 and rep["win_rate_pct"] == 100
    assert rep["realized_pnl"] > 0
    with pytest.raises(ValueError):
        b.fill("AAPL", "sell", 1, 110)  # no shorting


def test_max_drawdown():
    assert max_drawdown_pct([100, 120, 90, 110]) == pytest.approx(25.0)


def test_books_are_isolated(env):
    cfg, j, g = env
    started(j, PB)
    assert not g.check("breakout", buy(), acct(), 100, NOW).approved  # never started
    b1, b2 = PaperBroker(cfg, j, PB), PaperBroker(cfg, j, "breakout")
    b1.fill("AAPL", "buy", 10, 100)
    assert b2.account().positions == [] and b2.cash == cfg.start_cash


def test_unknown_strategy_rejected_and_live_not_paper(env):
    cfg, j, g = env
    assert "unknown strategy" in g.check("nope", buy(), acct(), 100, NOW).reasons[0]
    with pytest.raises(ValueError):
        PaperBroker(cfg, j, LB)


def test_only_one_live_allowed():
    with pytest.raises(ValueError):
        Config(strategies={"a": "live", "b": "live"})


def test_decision_stores_explanation_and_dashboard(env):
    from trader import dashboard
    cfg, j, g = env
    started(j, PB)
    b = PaperBroker(cfg, j, PB)
    p = buy(qty=10, rationale="RSI2 oversold in uptrend", signals={"rsi2": 6, "rule": "entry"})
    d = g.check(PB, p, b.account(), 100, NOW)
    b.fill("AAPL", "buy", d.qty, 100, decision_id=d.id)
    snap = dashboard.snapshot(j, cfg)
    dec = snap["books"][PB]["decisions"][0]
    assert dec["rationale"].startswith("RSI2") and dec["signals"]["rsi2"] == 6
    assert dec["fills"] and snap["books"][PB]["positions"][0]["symbol"] == "AAPL"
    assert [x["book"] for x in snap["overview"]["books"]] == cfg.books
    html = dashboard.render_html(snap)
    assert "RSI2 oversold" in html and "/*__SNAPSHOT__*/" not in html


# ---------------- live strategy: skill-aligned rules ----------------
PH = {k: {"pass": True, "evidence": "ok"} for k in
      ("market", "fundamentals", "trend_template", "extension", "stage2", "momentum")}
OV = {"momentum-quality": dict(
    max_position_pct=16.0, max_invested_pct=80.0, risk_per_trade_pct=1.0, max_positions=5,
    limit_only=True, require_stop=True, require_regime=True, one_per_sector=True, max_ai_names=2,
    no_add=True, required_phases=list(PH), confirm_days=2)}


@pytest.fixture
def live(tmp_path):
    cfg = Config(db_path=str(tmp_path / "l.db"), allowed_account_id="ACC1", overrides=OV)
    j = Journal(cfg.db_path)
    controls.start(j, LB, 30, NOW.date())
    j.set("market_regime", "RISK-ON"); j.set("market_regime_ts", (NOW + timedelta(days=3)).isoformat())
    return cfg, j, RiskGuard(cfg, j)


def lbuy(sym="NVDA", qty=100, px=100.0, stop=94.0, sector="Semis", ai=False, phases=None, **sig):
    return Proposal(sym, "buy", qty, "limit", px, stop_price=stop, rationale="r",
                    signals={"phases": phases if phases is not None else PH, "sector": sector,
                             "is_ai": ai, **sig})


def lacct(equity=10_000, cash=10_000, positions=()):
    return Account("ACC1", equity, cash, list(positions))


LATER = NOW + timedelta(days=3)  # past the 2-day confirm period


def test_live_happy_path_sizing_by_1pct_risk(live):
    cfg, j, g = live
    d = g.check(LB, lbuy(stop=90), lacct(), 100, LATER)
    assert d.approved and d.qty == 10  # $100 risk / $10 per share (cap would allow 16)
    d = g.check(LB, lbuy("AMD", stop=94), lacct(), 100, LATER + timedelta(minutes=10))
    assert d.qty == 16  # position cap 16% of 10k @ $100


def test_live_market_order_and_missing_stop_rejected(live):
    cfg, j, g = live
    mkt = lbuy(); mkt.order_type = "market"
    assert "limit orders only" in g.check(LB, mkt, lacct(), 100, LATER).reasons[0]
    assert not g.check(LB, lbuy(stop=None), lacct(), 100, LATER + timedelta(minutes=10)).approved


def test_live_requires_fresh_risk_on_regime(live):
    cfg, j, g = live
    j.set("market_regime", "RISK-OFF")
    assert "RISK-ON" in g.check(LB, lbuy(), lacct(), 100, LATER).reasons[0]
    j.set("market_regime", "RISK-ON")
    j.set("market_regime_ts", NOW.isoformat())  # 3 days old at check time => stale
    assert "stale" in g.check(LB, lbuy(), lacct(), 100, LATER + timedelta(minutes=1)).reasons[0]
    j.set("market_regime_ts", LATER.isoformat())
    assert g.check(LB, lbuy(), lacct(), 100, LATER + timedelta(minutes=2)).approved


def test_live_all_phases_must_pass(live):
    cfg, j, g = live
    bad = {**PH, "extension": {"pass": False, "evidence": "LATE 1.8 ATR"}}
    d = g.check(LB, lbuy(phases=bad), lacct(), 100, LATER)
    assert not d.approved and "extension" in d.reasons[0]
    missing = {k: v for k, v in PH.items() if k != "stage2"}
    assert "stage2" in g.check(LB, lbuy(phases=missing), lacct(), 100, LATER + timedelta(minutes=10)).reasons[0]


def test_live_portfolio_rules(live):
    cfg, j, g = live
    t = LATER
    a = lacct()
    d = g.check(LB, lbuy("NVDA", sector="Semis", ai=True), a, 100, t); assert d.approved
    a = lacct(positions=[Position("NVDA", 10, 100)])
    t += timedelta(minutes=10)
    assert "sector" in g.check(LB, lbuy("AMD", sector="Semis"), a, 100, t).reasons[0]       # one per sector
    assert "no averaging" in g.check(LB, lbuy("NVDA", sector="Other"), a, 100, t).reasons[0]
    d = g.check(LB, lbuy("PLTR", sector="Software", ai=True), a, 100, t); assert d.approved   # 2nd AI ok
    a = lacct(positions=[Position("NVDA", 10, 100), Position("PLTR", 10, 100)])
    # make PLTR a recorded AI holding too
    t += timedelta(minutes=10)
    assert "AI names" in g.check(LB, lbuy("ANET", sector="Networking", ai=True), a, 100, t).reasons[0]
    full = lacct(equity=100_000, cash=60_000, positions=[Position(s, 1, 100) for s in "ABCDE"])
    assert "max positions" in g.check(LB, lbuy("ZZZ", sector="X9"), full, 100, t + timedelta(minutes=1)).reasons[0]


def test_live_earnings_halves_position_cap(live):
    cfg, j, g = live
    d = g.check(LB, lbuy(stop=99, earnings_days=5), lacct(), 100, LATER)
    assert d.qty == 8 and any("halved" in w for w in d.warnings)  # 8% of 10k @ $100


def test_confirm_period_flags_orders_then_auto(live):
    cfg, j, g = live
    d = g.check(LB, lbuy(), lacct(), 100, NOW + timedelta(hours=1))
    assert d.approved and d.needs_user_approval          # no fill yet: clock hasn't started
    j.set(f"{LB}:first_fill_date", NOW.date().isoformat())
    d1 = g.check(LB, lbuy("MSFT", sector="Y"), lacct(), 100, NOW + timedelta(hours=2))
    assert d1.needs_user_approval                        # day 1 after first fill
    d2 = g.check(LB, lbuy("AMD", sector="X"), lacct(), 100, LATER)
    assert d2.approved and not d2.needs_user_approval


def test_record_gate_requires_user_approval(live, tmp_path):
    import json, sys
    from trader.cli import main
    cfg, j, g = live
    conf = tmp_path / "c.toml"
    conf.write_text(f'db_path = "{cfg.db_path}"\nallowed_account_id = "ACC1"\n'
                    '[overrides.momentum-quality]\nconfirm_days = 2\n')
    d = g.check(LB, lbuy(), lacct(), 100, datetime.now(timezone.utc))
    base = ["--config", str(conf), "record", "--book", LB, "--symbol", "NVDA", "--side", "buy",
            "--qty", "10", "--price", "100"]
    assert main(base) == 3                                   # live fill needs decision id
    assert main(base + ["--decision-id", str(d.id)]) == 3    # confirm period, not approved
    assert main(["--config", str(conf), "approve", "--decision-id", str(d.id)]) == 0
    assert main(base + ["--decision-id", str(d.id)]) == 0
    assert len(j.fills(LB)) == 1




# ---------------- Phase 0: read-only Robinhood client ----------------
def test_read_only_guard_blocks_every_non_read_tool():
    import asyncio
    from trader.robinhood.client import ReadOnlySession, WriteBlocked, is_read_only

    calls = []

    class FakeSession:
        async def call_tool(self, name, arguments):
            calls.append(name)
            class R:
                isError = False
                structuredContent = None
                content = [type("C", (), {"text": '{"ok": true}'})()]
            return R()

    rh = ReadOnlySession(FakeSession())
    for bad in ("place_equity_order", "cancel_equity_order", "place_option_order", "exercise_option",
                "create_alert", "add_to_watchlist", "run_scan", "review_equity_order", "search"):
        assert not is_read_only(bad)
        with pytest.raises(WriteBlocked):
            asyncio.run(rh.call(bad))
    assert calls == []                                    # nothing reached the server
    assert asyncio.run(rh.call("get_accounts")) == {"ok": True}
    assert calls == ["get_accounts"]


def test_agentic_account_selection_and_masking():
    from trader.robinhood.client import agentic_accounts, mask
    payload = {"data": {"accounts": [{"account_number": "871300943", "agentic_allowed": False},
                                     {"account_number": "929366227", "agentic_allowed": True, "nickname": "Agentic"}]}}
    got = agentic_accounts(payload)
    assert [a["account_number"] for a in got] == ["929366227"]
    assert mask("929366227") == "••••6227" and "9293" not in mask("929366227")


def test_token_storage_is_private_and_roundtrips(tmp_path):
    import asyncio, os, stat
    pytest.importorskip("mcp")
    from mcp.shared.auth import OAuthToken
    from trader.robinhood.client import FileTokenStorage
    st = FileTokenStorage(tmp_path / "sub" / "tok.json")
    assert asyncio.run(st.get_tokens()) is None
    asyncio.run(st.set_tokens(OAuthToken(access_token="secret-abc", token_type="Bearer")))
    assert stat.S_IMODE(os.stat(st.path).st_mode) == 0o600
    assert asyncio.run(st.get_tokens()).access_token == "secret-abc"


def test_auth_check_reports_fail_closed_without_network(capsys, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from trader.robinhood import authcheck

    @asynccontextmanager
    async def boom(*a, **k):
        raise ConnectionError("no network")
        yield
    monkeypatch.setattr(authcheck, "connect_read_only", boom)
    assert asyncio.run(authcheck.run_auth_check(Config())) == 1
    assert "NOT READY" in capsys.readouterr().out


def test_probe_outline_hides_values_and_extra_allowed_is_explicit():
    import asyncio
    from trader.robinhood.client import ReadOnlySession, WriteBlocked
    from trader.robinhood.probe import outline

    txt = "\n".join(outline({"data": {"total_value": "2002.22", "cash": 1588.03, "state": "filled",
                                      "account_number": "929366227", "orders": [{"side": "buy", "qty": "3"}]}}))
    assert "2002" not in txt and "1588" not in txt and "929366227" not in txt   # no amounts / account numbers
    assert 'state: string e.g. "filled"' in txt and "orders: list[1]" in txt

    class S:
        async def call_tool(self, n, a): raise AssertionError("must not reach server")
    rh = ReadOnlySession(S(), {"run_scan"})
    for bad in ("place_equity_order", "create_scan", "update_scan_filters", "cancel_equity_order"):
        with pytest.raises(WriteBlocked):
            asyncio.run(rh.call(bad))


# ---------------- strategy facts (momentum-quality) ----------------
def _bars(closes, vols=None, spread=1.0):
    vols = vols or [1000] * len(closes)
    return [{"close": c, "high": c + spread, "low": c - spread, "volume": v} for c, v in zip(closes, vols)]


def test_extension_labels_and_macd():
    from trader.strategy.momentum import extension, macd_state
    assert extension(99, 100, 2)[1] == "BELOW_21EMA"
    assert extension(100.5, 100, 2)[1] == "CLEAN"
    assert extension(102, 100, 2)[1] == "ACCEPTABLE"      # +1.0 ATR
    assert extension(103.5, 100, 2)[1] == "LATE"          # +1.75
    assert extension(105, 100, 2)[1] == "TAKE_PROFITS"    # +2.5
    assert macd_state([0.2, 0.5]) == "POSITIVE_RISING" and macd_state([-0.1, -0.3]) == "NEGATIVE_FALLING"


def test_trend_template_requires_all_seven():
    from trader.strategy.momentum import trend_template
    ok = trend_template(100, 95, 90, 80, 78, 105, 60)
    assert ok["pass"] and ok["pct_below_52w_high"] == 4.8
    for bad in (dict(sma50=101), dict(sma200_month_ago=81), dict(high52=140), dict(low52=90)):
        args = dict(price=100, sma50=95, sma150=90, sma200=80, sma200_month_ago=78, high52=105, low52=60) | bad
        assert not trend_template(**args)["pass"], bad


def test_regime_and_distribution_days():
    from trader.strategy.momentum import regime, distribution_days, new_4w_low_recent
    good = dict(price=612, sma50=598, sma200=560, ema50=600, ema100=590, dist_days=2, new_low_5d=False)
    assert regime(good, good).state == "RISK-ON"
    for bad in (dict(price=550), dict(dist_days=4), dict(new_low_5d=True), dict(ema50=585)):
        r = regime(good | bad, good)
        assert r.state == "RISK-OFF" and r.reasons
    closes = [100] * 10 + [99, 98, 97, 96]                  # 4 down days
    vols = [1000] * 10 + [1100, 1200, 1300, 1400]            # each on higher volume
    assert distribution_days(_bars(closes, vols)) == 4
    flat = _bars([100.0] * 25)
    assert new_4w_low_recent(flat) is False
    dip = _bars([100.0] * 20 + [90.0] * 5)
    assert new_4w_low_recent(dip) is True


# ---------------- Phase 1: market parsing, analyst facts check, pipeline ----------------
def test_market_parsers_and_shape_errors():
    from trader.robinhood import market as mk
    q = {"data": {"results": [{"quote": {"symbol": "SPY", "last_trade_price": "600.5", "bid_price": "600.4",
                                          "ask_price": "600.6", "previous_close": "599"}}]}}
    assert mk.parse_quotes(q)["SPY"]["ask"] == 600.6
    ind = {"data": {"indicators": [{"series": [{"time": "2026-10-01T00:00:00Z", "value": "10"},
                                               {"time": "2026-10-02T00:00:00Z", "value": "12"}]}]}}
    assert mk.latest_value(ind) == 12.0
    rev = {"data": {"indicators": [{"series": [{"time": "2026-10-02T00:00:00Z", "value": "12"},
                                               {"time": "2026-10-01T00:00:00Z", "value": "10"}]}]}}
    assert mk.latest_value(rev) == 12.0                       # newest-first input is re-ordered
    macd = {"data": {"indicators": [{"series": [{"time": "t1", "macd": 1, "signal": 0.5, "histogram": 0.5}]}]}}
    assert mk.latest_value(macd, "hist") == 0.5
    bars = {"data": {"results": [{"symbol": "X", "bars": [{"begins_at": "2026-10-01", "close_price": "10", "high_price": "11",
                                                          "low_price": "9", "volume": "100"}]}]}}
    assert mk.parse_bars(bars, "X")[0]["close"] == 10.0
    with pytest.raises(mk.ShapeError) as e:
        mk.parse_bars({"data": {"results": [{"symbol": "X", "bars": [{"weird_close": 1, "h": 1, "l": 1, "v": 1}]}]}}, "X")
    assert "found keys" in str(e.value) and "weird_close" in str(e.value)    # self-diagnosing
    assert mk.parse_earnings({"data": {"results": [{"symbol": "A", "report": {"date": "2026-10-20"}},
                                                    {"symbol": "A", "report": {"date": "2026-10-10"}}]}}) == {"A": "2026-10-10"}


def _ctx(label="CLEAN", macd="POSITIVE_RISING", tt_pass=True, regime="RISK-ON", ask=100.0, shares=3):
    tt = {"checks": {"sma200_rising": True}, "pass": tt_pass, "pct_below_52w_high": 5.0, "pct_above_52w_low": 80.0}
    cand = {"symbol": "VRT", "price": ask, "ask": ask, "trend_template": tt, "extension_atr": 0.4, "extension_label": label,
            "macd": macd, "rsi14": 60, "volume_vs_avg30": 1.6, "rs_vs_spy_30d": 12.0, "shares_at_position_size": shares,
            "sector": "AI Infrastructure"}
    return {"asof": "x", "account_number": "ACC1",
            "account": {"equity": 2000.0, "cash": 1500.0, "buying_power": 1500.0, "position_size": 320.0, "positions": []},
            "regime": {"state": regime, "reasons": ["r"], "qqq_distribution": False,
                       "facts": {k: {"price": 600.0, "sma50": 590.0, "sma200": 550.0, "ema50": 595.0, "ema100": 585.0,
                                     "dist_days": 2, "dist_dates": ["2026-09-30"], "new_low_5d": False, "last_bar": "2026-10-02"}
                                 for k in ("SPY", "QQQ")}},
            "funnel": {"scan_matches": 1, "affordable": 1, "after_earnings_filter": 1, "finalists": 1},
            "candidates": [cand], "holdings": [], "candidate_errors": []}


def _decision(qty=3, limit=100.10, passed=True):
    from trader.analyst import DecisionOut, PhaseOut, PHASES
    return DecisionOut(symbol="VRT", action="buy", qty=qty, limit_price=limit, stop_price=94.0, target_price=118.0,
                       rationale="breakout", sector="AI Infrastructure", is_ai=True, setup="box", earnings_days=999,
                       exit_rule="", phases={k: PhaseOut(passed=passed, evidence="e") for k in PHASES})


def test_verify_against_facts_overrides_model_claims():
    from trader.analyst import verify_against_facts
    ok, problems, phases = verify_against_facts(_decision(), _ctx())
    assert ok and phases["trend_template"]["pass"] and "CLEAN" in phases["extension"]["evidence"]
    for kwargs in (dict(label="LATE"), dict(macd="NEGATIVE_FALLING"), dict(tt_pass=False), dict(regime="RISK-OFF")):
        ok, problems, _ = verify_against_facts(_decision(), _ctx(**kwargs))   # model says passed=True; code says no
        assert not ok and problems, kwargs
    ok, problems, _ = verify_against_facts(_decision(qty=9), _ctx())
    assert not ok and "exceeds" in problems[0]
    ok, problems, _ = verify_against_facts(_decision(limit=103.0), _ctx())
    assert not ok and "ask" in problems[0]


def test_pipeline_dry_run_journals_and_never_orders(tmp_path):
    import asyncio
    from contextlib import asynccontextmanager
    from trader.analyst import DecisionSet
    from trader.pipeline import run_pipeline
    cfg = Config(db_path=str(tmp_path / "p.db"), allowed_account_id="ACC1", overrides=OV | {"momentum-quality": OV["momentum-quality"] | {"confirm_days": 0}})
    j = Journal(cfg.db_path)
    now = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
    controls.start(j, LB, 30, now.date())
    lines = []

    @asynccontextmanager
    async def fake_connect(*a, **k):
        yield object()

    async def fake_build(rh, cfg, book, log, now):
        return _ctx()

    good, bad = _decision(), _decision(limit=105.0)
    out = asyncio.run(run_pipeline(cfg, j, True, connect=fake_connect, build=fake_build, log=lines.append, now=now,
                                   analyst_fn=lambda c, x: (DecisionSet(summary="s", decisions=[good, bad]),
                                                            {"input_tokens": 1000, "output_tokens": 500, "cost_usd": 0.014})))
    text = "\n".join(lines)
    assert out == 0 and "WOULD PLACE (dry run): BUY 3 VRT" in text and "REJECTED by fact check" in text
    assert "Nothing was sent to Robinhood" in text
    rows = j.db.execute("SELECT approved FROM decisions WHERE book=?", (LB,)).fetchall()
    assert sorted(r["approved"] for r in rows) == [0, 1]
    assert j.get("market_regime") == "RISK-ON"
    # daily cost cap blocks a second call
    cfg2 = Config(**{**cfg.__dict__, "analyst_daily_cap_usd": 0.01})
    lines.clear()
    assert asyncio.run(run_pipeline(cfg2, j, True, connect=fake_connect, build=fake_build, log=lines.append, now=now,
                                    analyst_fn=lambda c, x: (_ for _ in ()).throw(AssertionError("must not call")))) == 1
    assert "daily cap" in "\n".join(lines)


def test_api_key_storage_private(tmp_path, monkeypatch):
    import os, stat
    from trader import analyst
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    kf = tmp_path / "k" / "key"
    analyst.save_api_key("sk-ant-test123", kf)
    assert stat.S_IMODE(os.stat(kf).st_mode) == 0o600
    monkeypatch.setattr(analyst, "KEY_FILE", kf)
    assert analyst.load_api_key() == "sk-ant-test123"
    monkeypatch.setattr(analyst, "KEY_FILE", tmp_path / "missing")
    with pytest.raises(analyst.AnalystError):
        analyst.load_api_key()


def test_analyst_schema_is_valid_json_schema():
    import jsonschema
    from trader.analyst import SCHEMA, DecisionSet
    ds = DecisionSet(summary="s", decisions=[_decision()])
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    payload = ds.model_dump(); payload["decisions"][0]["phases"] = {k: v for k, v in payload["decisions"][0]["phases"].items()}
    jsonschema.validate(payload, SCHEMA)


def test_existing_holding_sector_and_ai_flag_reach_the_guard(live):
    cfg, j, g = live
    held = [Position("ANET", 2, 211.0, sector="Electronic Technology", is_ai=True)]
    a = lacct(equity=2000, cash=1500, positions=held)
    d = g.check(LB, lbuy("MRVL", px=280, sector="Electronic Technology", ai=True, stop=265), a, 280, LATER)
    assert not d.approved and "sector Electronic Technology already held" in d.reasons[0]
    d = g.check(LB, lbuy("PAYC", px=244, sector="Technology Services", ai=False, stop=230, qty=1), a, 244, LATER + timedelta(minutes=10))
    assert d.approved


def test_distribution_dates_and_config_ai_list():
    from trader.strategy.momentum import distribution_dates
    bars = _bars([100, 100, 99, 98], [1000, 1000, 1100, 1200])
    for i, b in enumerate(bars):
        b["t"] = f"2026-10-0{i + 1}"
    assert distribution_dates(bars) == ["2026-10-03", "2026-10-04"]
    assert "ANET" in Config().ai_symbols


# ---------------- build_context end to end against a synthetic Robinhood ----------------
class FakeRH:
    """Answers every read tool the data step uses with replies shaped like the real ones. ext: ATRs above the 21 EMA."""
    def __init__(self, ext_by_symbol):
        self.ext, self.calls = ext_by_symbol, []

    async def call(self, tool, args=None):
        args = args or {}
        self.calls.append(tool)
        if tool == "get_accounts":
            return {"data": {"accounts": [{"account_number": "ACC1", "agentic_allowed": True}]}}
        if tool == "get_portfolio":
            return {"data": {"total_value": "2000", "cash": "1500", "buying_power": {"unleveraged_buying_power": "1500"}}}
        if tool == "get_equity_positions":
            return {"data": {"positions": [{"symbol": "HELD", "quantity": "2", "average_buy_price": "100"}]}}
        if tool == "get_equity_quotes":
            return {"data": {"results": [{"quote": {"symbol": s, "last_trade_price": "100", "bid_price": "99.9", "ask_price": "100.1",
                                                    "previous_close": "99"}} for s in args["symbols"]]}}
        if tool == "run_scan":
            return {"data": {"result": {"results": [{"ticker": t, "columns": {"x": 1}} for t in ("AAA", "BBB", "CCC", "DDD")]}}}
        if tool == "get_equity_fundamentals":
            return {"data": {"results": [{"symbol": s, "market_cap": "5e9", "high_52_weeks": "105", "low_52_weeks": "50",
                                          "average_volume_30_days": "1000", "volume": "1500", "sector": "Tech" if s in ("AAA", "BBB") else "Health",
                                          "industry": "x", "pe_ratio": "20"} for s in args["symbols"]]}}
        if tool == "get_earnings_calendar":
            return {"data": {"results": [{"symbol": "DDD", "report": {"date": (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")}}]}}
        if tool == "get_equity_historicals":
            mult = {"SPY": 1.0005, "QQQ": 1.0005, "AAA": 1.004, "BBB": 1.002, "CCC": 1.003, "HELD": 1.001}
            res = []
            for s in args["symbols"]:
                c, bars = 100.0, []
                for i in range(60):
                    c *= mult.get(s, 1.001)
                    bars.append({"begins_at": f"2026-08-{(i % 28) + 1:02d}T00:00:00Z" if False else f"2026-{7 + i // 28:02d}-{(i % 28) + 1:02d}T00:00:00Z",
                                 "close_price": str(c), "high_price": str(c + 1), "low_price": str(c - 1), "volume": "1000"})
                res.append({"symbol": s, "bars": bars})
            return {"data": {"results": res}}
        if tool == "get_equity_technical_indicators":
            sym, typ, out = args["symbol"], args["type"], args["output"]
            ext = self.ext.get(sym, 0.3)
            val = {("ema", 21): 100.0 - ext * 2.0, ("atr", 14): 2.0, ("sma", 50): 95.0, ("sma", 150): 85.0, ("sma", 200): 75.0,
                   ("ema", 50): 96.0, ("ema", 100): 90.0, ("rsi", None): 60.0}.get((typ, args.get("period")), 1.0)
            if typ == "macd":
                series = [{"time": f"t{i}", "macd": 1, "signal": 0.5, "histogram": 0.1 * (i + 1)} for i in range(3)]
            elif out.startswith("last"):
                series = [{"time": f"2026-09-0{i + 1}T00:00:00Z", "value": val - (3 - i)} for i in range(3)]
            else:
                series = [{"time": "2026-10-02T00:00:00Z", "value": val}]
            if sym in ("SPY", "QQQ") and (typ, args.get("period")) == ("sma", 50):
                series = [{"time": "2026-10-02T00:00:00Z", "value": 99.0}]
            return {"data": {"indicators": [{"type": typ, "series": series}]}}
        raise AssertionError(f"unexpected tool {tool}")


def test_build_context_filters_by_strength_sector_extension_and_earnings():
    import asyncio
    from trader.robinhood.market import build_context
    cfg = Config(allowed_account_id="ACC1", max_candidates=5, max_per_sector_candidates=1)
    rh = FakeRH({"AAA": 3.0, "BBB": 0.3, "CCC": 0.4})            # AAA is extended; BBB, CCC are clean
    now = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    ctx = asyncio.run(build_context(rh, cfg, "momentum-quality", lambda *_: None, now))
    syms = [c["symbol"] for c in ctx["candidates"]]
    assert "DDD" not in syms                                       # earnings in 3 days
    assert "AAA" not in syms and ctx["extended_skipped"][0]["symbol"] == "AAA"
    assert set(syms) == {"BBB", "CCC"}      # extended AAA does not crowd BBB out of the Tech slot (cap is 1 per sector)
    assert ctx["regime"]["state"] in ("RISK-ON", "RISK-OFF") and ctx["account_number"] == "ACC1"
    assert ctx["holdings"] and ctx["holdings"][0]["symbol"] == "HELD"
    assert ctx["funnel"]["scan_matches"] == 4 and "run_scan" in rh.calls
    assert not any(t.startswith(("place_", "cancel_")) for t in rh.calls)


def test_exit_flags_follow_the_skill():
    from trader.strategy.momentum import exit_flags, stop_pct_for
    assert stop_pct_for(1.0, 16.0) == 6.25
    f = exit_flags(price=190, avg_cost=203.86, sma50=193.4, macd="POSITIVE_RISING", ext_label="CLEAN", stop_pct=6.25, earnings_days=None)
    assert [x.split(":")[0] for x in f["must_sell"]] == ["HARD_STOP", "BELOW_50D_MA"]
    ok = exit_flags(207, 203.86, 193.4, "POSITIVE_RISING", "ACCEPTABLE", 6.25, None)
    assert ok["must_sell"] == [] and ok["pnl_pct"] == 1.54
    assert exit_flags(210, 203.86, 190, "NEGATIVE_FALLING", "CLEAN", 6.25, None)["tighten"]
    assert exit_flags(236, 200, 190, "POSITIVE_RISING", "TAKE_PROFITS", 6.25, None)["partial"][0].startswith("TARGET_15")
    assert exit_flags(260, 200, 190, "POSITIVE_RISING", "CLEAN", 6.25, 1)["must_sell"][0].startswith("EARNINGS_IMMINENT")


def test_pipeline_forces_a_mandatory_exit_the_analyst_missed(tmp_path):
    import asyncio
    from contextlib import asynccontextmanager
    from trader.analyst import DecisionSet
    from trader.pipeline import run_pipeline
    cfg = Config(db_path=str(tmp_path / "p.db"), allowed_account_id="ACC1", overrides=OV | {"momentum-quality": OV["momentum-quality"] | {"confirm_days": 0}})
    j = Journal(cfg.db_path)
    now = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
    controls.start(j, LB, 30, now.date())
    ctx = _ctx()
    ctx["candidates"], ctx["account"]["stop_pct"] = [], 6.25
    ctx["account"]["positions"] = [{"symbol": "HELD", "qty": 2.0, "avg": 100.0}]
    ctx["holdings"] = [{"symbol": "HELD", "qty": 2.0, "avg_cost": 100.0, "price": 92.0, "bid": 91.9, "sma50": 95.0, "macd": "NEGATIVE_FALLING",
                        "extension_label": "BELOW_21EMA", "pnl_pct": -8.0, "sector": "Tech",
                        "exit_flags": {"must_sell": ["HARD_STOP: x", "BELOW_50D_MA: y"], "tighten": [], "partial": [], "pnl_pct": -8.0}}]
    lines = []

    @asynccontextmanager
    async def fake_connect(*a, **k):
        yield object()

    async def fake_build(rh, cfg, book, log, now):
        return ctx

    out = asyncio.run(run_pipeline(cfg, j, True, connect=fake_connect, build=fake_build, log=lines.append, now=now,
                                   analyst_fn=lambda c, x: (DecisionSet(summary="hold", decisions=[]),   # analyst misses it
                                                            {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0})))
    text = "\n".join(lines)
    assert out == 0 and "FORCED EXIT by code (dry run): SELL 2 HELD limit 91.85" in text and "1 order(s) would be placed" in text
    row = j.db.execute("SELECT approved, side, qty FROM decisions WHERE book=?", (LB,)).fetchone()
    assert (row["approved"], row["side"], row["qty"]) == (1, "sell", 2)
