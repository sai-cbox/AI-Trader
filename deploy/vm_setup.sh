#!/bin/sh
# Run on a fresh Ubuntu/Debian VM as a normal user (not root). Installs the app and a cron job for `trader tick`.
# Afterwards copy your two secret files from the Mac (see docs/RUNBOOK.md "Move to a cloud VM").
set -e
sudo apt-get update -y && sudo apt-get install -y git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
[ -d AI-Trader ] || git clone -b claude/jolly-goodall-8vwsoh https://github.com/sai-cbox/AI-Trader.git
cd AI-Trader
uv venv --python 3.12 && . .venv/bin/activate && uv pip install -e ".[exec]"
mkdir -p data ~/.config/ai-trader && chmod 700 ~/.config/ai-trader
# Cron runs in UTC; tick converts to US/Eastern itself, so one schedule covers daylight saving.
( crontab -l 2>/dev/null | grep -v "trader tick"; echo "*/5 13-21 * * 1-5 cd $PWD && .venv/bin/trader tick >> data/tick.log 2>&1" ) | crontab -
.venv/bin/trader start --paper || true
.venv/bin/trader start --book momentum-quality || true
echo "Done. Now copy the secret files, then run: .venv/bin/trader data-check"
