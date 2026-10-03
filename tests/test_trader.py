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
