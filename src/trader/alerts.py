"""Phone alerts through ntfy.sh (free, no account). Topic = a long random string you pick; anyone who knows it can read it,
so never put secrets or dollar balances in a message. Disabled when ntfy_topic is empty. Failures never stop trading."""
from __future__ import annotations

import urllib.request

from .config import Config


def notify(cfg: Config, title: str, message: str, priority: str = "default", opener=urllib.request.urlopen) -> bool:
    if not cfg.ntfy_topic:
        return False
    req = urllib.request.Request(f"https://ntfy.sh/{cfg.ntfy_topic}", data=message.encode("utf-8"), method="POST",
                                 headers={"Title": title.encode("ascii", "replace").decode(), "Priority": priority})
    try:
        opener(req, timeout=10).close()
        return True
    except Exception:
        return False
