"""Daily summary email over SMTP (Gmail app password). The password lives in a 0600 file, never in config or the repo.
Sending goes straight from your machine to the SMTP server; nothing passes through any third-party service."""
from __future__ import annotations

import html
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from .config import Config
from .journal import Journal

PW_FILE = Path.home() / ".config" / "ai-trader" / "smtp_password"


class MailError(Exception):
    pass


def save_password(pw: str, path: Path = PW_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(pw.replace(" ", "").strip())  # Google shows app passwords in 4-letter groups
    os.chmod(path, 0o600)


def load_password(path: Path = PW_FILE) -> str:
    pw = os.environ.get("TRADER_SMTP_PASSWORD") or (path.read_text().strip() if path.exists() else "")
    if not pw:
        raise MailError("no email password: run `trader set-email-password`")
    return pw


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:+.2f}%"


def _money(v) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


def build_summary(st: dict, now: datetime) -> tuple[str, list[str]]:
    """(subject, lines) from dashboard.full_state(). Plain facts only, same numbers as the dashboard."""
    a, lr, L = st.get("account"), st.get("live_run"), st["limits"]
    regime = lr["regime"]["state"] if lr else "not checked"
    live = st["books"][st["live_book"]] if st.get("live_book") else None
    subject = f"AI-Trader daily summary {now.date().isoformat()}: market {regime}"
    out = [f"AI-Trader daily summary, {now.strftime('%a %b %d, %H:%M UTC')}", ""]
    out.append("ACCOUNT (Robinhood Agentic)")
    if a:
        out += [f"  Value {_money(a['total'])}, cash {_money(a['cash'])}, invested {_money(a['invested'])}  (read {a['updated_at'][:16]} UTC)"]
        for h in a["holdings"]:
            flags = ((h.get("exit_flags") or {}).get("must_sell")) or []
            out.append(f"  {h['symbol']}: {h.get('qty')} sh, P&L {_pct(h.get('pnl_pct'))}, "
                       + ("SELL SIGNAL: " + "; ".join(flags) if flags else "no exit rule triggered"))
    else:
        out.append("  no account data yet (run `trader data-check`)")
    out += ["", f"MARKET: {regime}"]
    if lr:
        out += ["  " + r for r in lr["regime"]["reasons"]]
        if lr.get("summary"):
            out += ["", "ANALYST: " + lr["summary"]]
    out += ["", "STRATEGIES (return since start, closed trades, open positions, approved/rejected decisions)"]
    for b in st["order"]:
        s = st["books"][b]["summary"]
        out.append(f"  {b}{' [LIVE, decisions only]' if s['kind'] == 'live' else ' [paper]'}: {_pct(s['return_pct'])}, "
                   f"{s['trades']} closed, {s['open_positions']} open, {s['approved']}/{s['rejected']}")
    if st.get("spy"):
        out.append(f"  SPY over the same period: {_pct(st['spy'][-1]['ret_pct'])}")
    recent = []
    for b in st["order"]:
        for d in st["books"][b]["decisions"]:
            if d["ts"][:10] == now.date().isoformat():
                recent.append((d["ts"], f"  {b}: {d['side'].upper()} {d['approved_qty'] or d['requested_qty']} {d['symbol']} "
                               + ("approved" if d["approved"] else "REJECTED (" + "; ".join(d["risk_reasons"]) + ")")
                               + f" - {(d['rationale'] or '')[:140]}"))
    out += ["", "DECISIONS TODAY"] + ([x[1] for x in sorted(recent)] if recent else ["  none"])
    out += ["", "SAFETY",
            f"  Daily P&L {_pct(L['daily_pnl_pct'])} (limit -{L['daily_loss_limit_pct']}%), drawdown {L['drawdown_pct']:.2f}% (stop at {L['drawdown_stop_pct']}%), "
            f"orders today {L['orders_today']}/{L['max_orders_per_day']}, live orders {st['live_execution'].upper()}"
            + (", STOP FILE PRESENT" if st.get("stop_file") else "")]
    jobs = []
    for n, j in st["jobs"].items():
        jobs.append(f"  {n}: " + ("never run" if not j else f"{'OK' if j['ok'] else 'FAILED'} at {j['ts'][:16]} UTC"))
    out += ["", "JOBS"] + jobs
    out += ["", "Paper results are an upper bound (paper fills are slightly better than real ones). Nothing is ranked until 30+ closed trades."]
    return subject, out


def send_email(cfg: Config, subject: str, lines: list[str], smtp_cls=smtplib.SMTP_SSL, password: str | None = None) -> None:
    if not cfg.email_to:
        raise MailError("email_to is empty in config")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg.email_to, cfg.email_to
    msg.set_content("\n".join(lines))
    body = html.escape("\n".join(lines))
    msg.add_alternative(f'<pre style="font:14px/1.5 ui-monospace,Menlo,monospace;white-space:pre-wrap">{body}</pre>', subtype="html")
    pw = password if password is not None else load_password()
    try:
        with smtp_cls(cfg.smtp_host, cfg.smtp_port, context=ssl.create_default_context(), timeout=20) as s:
            s.login(cfg.email_to, pw)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("Gmail rejected the login. Use a 16-letter App Password (not your normal password), "
                        "with 2-Step Verification on.") from e
    except (OSError, smtplib.SMTPException) as e:
        raise MailError(f"could not send: {type(e).__name__}: {e}") from e


def daily_summary(cfg: Config, j: Journal, now: datetime, send=send_email) -> list[str]:
    from .dashboard import full_state
    subject, lines = build_summary(full_state(j, cfg, now), now)
    send(cfg, subject, lines)
    return lines
