# Five more families on Delta's liquid perps — pre-registration (2026-11-16)

Owner, 2026-11-16: "Search more families." Written before any of these rules was run. Same data, split, fills and
selection as `docs/intraday_families_prereg.md` (30 perps, 1m, liquid days, DEV 2026-03..07, TEST 2026-08..11, in at
the next open, out N minutes later, before fees, best DEV variant + direction is the only one judged on TEST).
Bar: TEST mean > 0, t >= 2.7, both TEST halves > 0. Twelve families will then have been tried on this data.

- H1 OPENING RANGE. The high / low of the first R minutes of the UTC day; the first later close outside it: follow.
  R in {30, 60}, hold in {120, 360} min.
- H2 FIRST PART OF THE DAY PREDICTS THE REST. Sign of the move from 00:00 to T; hold the next 8 hours.
  T in {8 h, 12 h}, only if the move is at least m in {0, 1}%.
- H3 PAIRS. BTC, ETH, SOL, XRP, six pairs. z = how far the log price ratio is from its 24-hour mean, in standard
  deviations; when |z| first reaches zt: sell the rich one and buy the cheap one, equal size.
  zt in {2, 3}, hold in {60, 240, 720} min. Result = the average of the two legs.
- H4 MARKET BREADTH. Share of traded coins that are up over the last hour (at least 10 coins) reaches thr or falls to
  1 - thr: trade BTC, ETH and SOL in that direction. thr in {80, 90}%, hold in {60, 240} min.
- H5 SLOW LEAD-LAG. BTC moved theta or more over the last hour: trade every other coin in the same direction.
  theta in {1, 2}%, hold in {60, 240} min, with and without "the coin has done less than half of BTC's move".
Each family is also read in its mirror direction. Expectation stated in advance: none passes.
