"""Where a profit-protecting stop belongs, given how far a trade has gone.

ONE IMPLEMENTATION, ADDED 2026-10-02. The stop ladder was written twice -- in
the paper broker (ticks) and in run_portfolio (minutes) -- and the prod pilot
adds a third consumer (the dry run's shadow recorder) and a second rule (a
trailing stop). Everything that moves a stop asks this module, so the paper
bot, the backtester and the dry run cannot drift into three rules that share a
name.

THE CONTRACT. Callers pass the favourable excursion AT ONE PRICE, in R against
the trade's ORIGINAL risk per unit, and apply the answer only if it TIGHTENS
the stop they hold. Under that contract a stateless function is enough for a
trail as well as a ladder: the running maximum of (excursion - trail) is
(peak - trail), so tighten-only application reproduces a peak-tracking trail
exactly. Rounding to the tick, in the protective direction, stays with the
caller, which knows the instrument.

THE TWO RULES.
  ladder  ((trigger_r, stop_r), ...): the highest rung whose trigger the
          excursion has reached sets the stop at stop_r.
  trail   (trail_after_r, trail_r): once the excursion reaches trail_after_r,
          the stop sits trail_r behind it. Delta's own trailing stop has no
          activation price, so the live version attaches the trail by one
          bracket edit at trail_after_r; this models that, not a trail from
          entry.
"""
from __future__ import annotations


def earned_stop_r(favourable_r: float, *,
                  ladder_rungs: tuple[tuple[float, float], ...] = (),
                  trail_after_r: float | None = None,
                  trail_r: float | None = None) -> float | None:
    """The stop, in R from entry, that this excursion has earned; None if none."""
    best: float | None = None
    for trigger_r, stop_r in ladder_rungs:
        if favourable_r < trigger_r:
            break                       # rungs ascend; nothing further is earned
        best = stop_r
    if trail_after_r is not None and trail_r is not None and favourable_r >= trail_after_r:
        trailed = favourable_r - trail_r
        best = trailed if best is None else max(best, trailed)
    return best


def tightens(side: int, candidate: float, current: float) -> bool:
    """True when `candidate` is strictly in the position's favour of `current`."""
    return candidate > current if side > 0 else candidate < current
