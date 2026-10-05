"""One idempotent command for a scheduler: `trader tick` runs whichever slot is due (US/Eastern) and records it so a slot
never runs twice a day. Call it every ~5 minutes on weekdays; between slots it does nothing.

morning 09:45  held names: overnight gaps / mandatory exits; no new positions
midday  12:30  exits only
entry   15:45  the only run that may open positions (breakout closes are known near the bell)
close   16:20  paper strategies + daily summary email

Everything is still DRY-RUN: no order code exists. On a market holiday the runs just see the previous session's data again
(harmless; costs a few cents of Claude)."""
from __future__ import annotations

from datetime import datetime, timedelta

from . import records
from .config import Config
from .journal import Journal
from .risk import ET

SLOTS = {"morning": (9, 45), "midday": (12, 30), "entry": (15, 45), "close": (16, 20)}
WINDOW = timedelta(minutes=40)  # a missed tick (sleep, reboot) still catches up within this window


def due_slot(j: Journal, now: datetime) -> str | None:
    et = now.astimezone(ET)
    if et.weekday() >= 5:
        return None
    day = et.date().isoformat()
    for name, (h, m) in SLOTS.items():
        start = et.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= et < start + WINDOW and j.get(f"tick:{name}") != day:
            return name
    return None


def run_tick(cfg: Config, j: Journal, now: datetime, log=print, run_live=None, run_paper=None, send_summary=None) -> str | None:
    import asyncio
    slot = due_slot(j, now)
    if not slot:
        return None
    day = now.astimezone(ET).date().isoformat()
    j.set(f"tick:{slot}", day)  # mark first: a crash must not loop and re-spend the Claude budget
    log(f"[tick] {slot} run, {now.astimezone(ET):%a %H:%M} ET")
    if run_live is None:
        from .pipeline import run_pipeline
        run_live = lambda entries: asyncio.run(run_pipeline(cfg, j, with_analyst=True, entries=entries, log=log, now=now))
    if run_paper is None:
        from .paper_runner import paper_run
        run_paper = lambda: asyncio.run(paper_run(cfg, j, log=log, now=now))
    if send_summary is None:
        from .mailer import MailError, daily_summary

        def send_summary():
            try:
                daily_summary(cfg, j, now)
                log("[tick] daily summary emailed")
            except MailError as e:
                log(f"[tick] summary email not sent: {e}")
    if slot in ("morning", "midday", "entry"):
        rc = run_live(slot == "entry")
    else:
        rc = run_paper()
        send_summary()
    records.record_job(j, f"tick-{slot}", rc == 0, f"exit code {rc}", now)
    return slot
