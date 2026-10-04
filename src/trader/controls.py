"""Per-strategy run state and the kill switch. Unset state means STOPPED."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .journal import Journal

RUNNING, PAUSED, STOPPED = "running", "paused", "stopped"


def stop_file(db_path: str) -> Path:
    return Path(db_path).parent / "STOP"


def get_state(j: Journal, book: str) -> str:
    return j.get(f"{book}:state", STOPPED)


def set_state(j: Journal, book: str, state: str, reason: str = "") -> None:
    j.set(f"{book}:state", state)
    j.set(f"{book}:state_reason", reason)
    j.event(book, f"state:{state}", reason)


def add_trading_days(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:  # holidays not modelled; window is approximate
            n -= 1
    return d


def start(j: Journal, book: str, window_days: int, today: date) -> date:
    end = add_trading_days(today, window_days)
    j.set(f"{book}:window_start", today.isoformat())
    j.set(f"{book}:window_end", end.isoformat())
    set_state(j, book, RUNNING, f"started; window ends {end}")
    return end


def window_ended(j: Journal, book: str, today: date) -> bool:
    end = j.get(f"{book}:window_end")
    return bool(end) and today > date.fromisoformat(end)


def kill_file_present(db_path: str) -> bool:
    return stop_file(db_path).exists()


def confirm_active(j: Journal, book: str, confirm_days: int, today: date) -> bool:
    """True while a book is inside its 'ask the user before every order' period (calendar days from start)."""
    if not confirm_days:
        return False
    start_s = j.get(f"{book}:window_start")
    return bool(start_s) and today < date.fromisoformat(start_s) + timedelta(days=confirm_days)
