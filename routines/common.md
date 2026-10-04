# Shared setup for every AI-Trader routine run

Dashboard (state store + UI): https://claude.ai/artifact/8QF5i8FpWPRuswKoJvcXMC  (call it DASH)
Account: only the Robinhood "Agentic" account (number in config/default.toml `allowed_account_id`).
Load tools first: ToolSearch "select:ArtifactData" plus the Robinhood tools you need.

## Boot (do this first, every run)
```
git clone --depth 1 -b claude/jolly-goodall-8vwsoh https://github.com/sai-cbox/AI-Trader /tmp/ait
cd /tmp/ait && export PYTHONPATH=src && T="python3 -m trader.cli"
```
1. Kill switch: ArtifactData get DASH collection `control` doc `engine`. Missing doc = all clear.
   Run `$T apply-control --stop <0|1> --pause <0|1>` with stop_all / pause_all from that doc.
2. Restore state for each book this routine touches (one ArtifactData get per book, doc `state/<book>`):
   save the doc JSON to a file and run `$T import-state --book <book> --file <file>`.
   (A missing doc is a fresh start; `import-state` with an empty file is a no-op.) Remember each doc's `version`.
3. After the run, for each book you changed: `$T export-state --book <book> --out /tmp/<book>.json`, then
   ArtifactData set DASH collection `state` doc `<book>` from that file, pinned with `if_version` (omit it only if the
   doc did not exist). On a version conflict: stop, do NOT overwrite; log the conflict in your final message.
4. Publish results for the page: `$T sync-docs --dir /tmp/out`, then ArtifactData set DASH collection `books`
   doc `<book>` from `/tmp/out/books/<book>.json` (read the doc first for its version; pin `if_version`).
5. If `$T status` shows a book stopped/paused, or the dashboard says stop_all: place NOTHING, say so in one line.

## Hard rules
- Every order goes through `$T check` first. Rejected (exit 2) = do nothing, never retry a tweaked order.
- Only equities, only limit orders, only the allow-listed Agentic account. Never options/crypto/shorts.
- Never skip or edit the risk guard, config, or the state files to get a trade through.
