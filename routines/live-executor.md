# Routine: Trading: Live executor (every 15 min, weekdays 6:30 AM - 1:00 PM PT)

Follow routines/common.md (Boot). Book: momentum-quality (LIVE). This is the ONLY routine allowed to place orders.

SCOPE (hard): account = the Agentic account only. Orders = LIMIT only, equities only. Place ONLY orders that exist as
docs in DASH collection `live_orders` with status `approved`. Never invent, resize upward, or modify an order.

1. Boot (kill switch first). If stop_all or the book is not `running` (paused allows SELLs only): place nothing; finish
   with one line unless there is something to report. If there is no `approved` doc, finish silently (no email).
2. Expiry: any `live_orders` doc with status awaiting_approval or approved whose `ts` is before today's market open
   (PT) => update it to status "expired". Never execute an expired order.
3. For each doc with status `approved` (oldest first), sells before buys:
   a. Fresh data: get_portfolio, get_equity_positions, get_equity_quotes. Build the account JSON and run
      `$T snapshot --book momentum-quality`.
   b. Re-check with the engine using the doc's own fields as the proposal (symbol, side, qty, limit_price=limit,
      stop_price=stop, target_price=target, signals incl. phases) and the LIVE quote:
      `$T check --book momentum-quality`. If exit 2, or the live quote moved more than 1.5% past the limit price
      (buy) => do NOT trade; set the doc status "rejected" with an `error` explaining why; continue.
   c. If the check says needs_user_approval and the doc's approval_mode is "user" (the user tapped Approve):
      `$T approve --decision-id <new decision_id>`. If approval_mode is not "user" and needs_user_approval is true:
      do NOT trade (approval is still required); set status back to "awaiting_approval".
   d. Use the qty returned by this check (never more than the doc's qty).
   e. `review_equity_order`, then `place_equity_order` (limit, day, the Agentic account). Poll get_equity_orders for
      up to ~2 minutes for the fill. If it fills (fully or partially), go to f. If unfilled, leave the order working
      only if still within the day; otherwise cancel it; set the doc "executed" only when filled.
   f. `$T record --book momentum-quality --symbol S --side buy|sell --qty <filled> --price <fill price>
      --decision-id <id> --ref <robinhood order id>`.
   g. Buys: place the protective GTC stop-limit SELL at the doc's `stop` (limit a few cents below) for the filled
      shares. If the tool cannot place it, say so loudly in the email ("NO STOP ORDER ON <SYMBOL>").
   h. Update the doc: status "executed", `executed`: {qty, price, order_id, ts}; on failure status "failed" + `error`.
4. Export state, sync-docs, publish per common.md (only if you changed anything).
5. FINAL MESSAGE = email to Sai, only when something was executed, failed, or expired: per order one line
   (BUY 3 VRT filled @ $110.04, stop $103.20 set) plus portfolio value and cash. Plain text, under 120 words.
