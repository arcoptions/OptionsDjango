"""Where the stop goes, and how much size that buys.

The exit study varies what happens after the trade works. This varies what
happens when it doesn't. The two are not independent: the stop sets R, R sets
the position size at a 2% risk budget, and size sets the rupees. A wider stop
survives more noise but buys fewer lots, and the earlier finding was that at a
fixed risk budget wider stops lost money. That was tested with the shipped
trail; it has never been tested jointly with the trail, and a wider stop with a
looser trail is a different animal from a wider stop with a tight one.

Everything runs through the full pipeline, so daily loss limits and cooldowns
respond to the change the way they would live.
"""
import os
import sys
from dataclasses import replace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))
os.chdir(ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_terminal.settings")

import django

django.setup()

from options_tracker.nifty_trail_strategy import nifty_trail_config

from exit_lab import book, run

STOPS = (0.08, 0.10, 0.12, 0.15, 0.20)
TRAILS = (0.5, 0.75, 1.0, 1.5)


def main():
    base = nifty_trail_config()
    grid = {}
    for stop in STOPS:
        config = replace(base, stop_percent=stop)
        for trail in TRAILS:
            grid[(stop, trail)] = book(run(config, trail_gap=trail))
            print(f"  ran stop {100 * stop:.0f}% trail {trail}R", flush=True)

    def table(title, cell):
        print(f"\n{title}\n")
        print(f"{'stop':>6}" + "".join(f"{'trail ' + str(t) + 'R':>22}" for t in TRAILS))
        for stop in STOPS:
            cells = ["n/a".rjust(22) if not grid[(stop, t)] else cell(grid[(stop, t)])
                     for t in TRAILS]
            print(f"{100 * stop:>5.0f}%" + "".join(cells))

    table("net rupees / max drawdown",
          lambda r: f"{r['net']:>9,.0f} /{r['dd']:>7,.0f}".rjust(22))
    table("trades and win rate",
          lambda r: f"{r['n']:>6} at {r['win']:>5.1f}%".rjust(22))


if __name__ == "__main__":
    main()
