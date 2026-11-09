# Streak and loss rules on rule A ladder — pre-registration

Written and committed 2026-11-09 BEFORE any of these rules was computed (owner: "yes" to testing pause-after-losses,
size-up-after-wins, pause-after-a-big-loss and a per-coin cooldown). Base: rule A, top 2 gainers, ladder 10/5/3
(`scripts/fade_ladder_account.py`, 45% position cap at 1x, day-sized, compounding, $1,500 at the start of each set).
A day's trades close by the next 00:00 UTC, so every rule uses only results known before the next entry.

## Rules (each on top of the ladder; settings searched only on development data)
- **R1 loss-streak pause:** after N losing trades in a row, take no trades for the next D days.  N ∈ {3, 4, 5}, D ∈ {1, 2}.
- **R2 win-streak size-up:** after W winning trades in a row, the next day's risk is 1.5 × the ladder's step
  (still capped by the 45% position limit).  W ∈ {3, 4, 5}.
- **R3 big-loss pause:** after any trade loses X R or more, take no trades the next day.  X ∈ {1.0, 1.2}.
- **R4 per-coin cooldown:** do not short a coin within C days after it was stopped out.  C ∈ {3, 7, 14}.
Skipped trades are not replaced.

## Step 1 — development: Binance 2021, 2022, 2023
For each rule, the setting with the highest pooled **R per unit of risk** (Σ risk × R ÷ Σ risk) over the three years is
chosen. The rule is **promoted** if that setting beats the plain ladder on R per unit of risk in at least 2 of 3 years.

## Step 2 — held-out, judged once: Binance 2024 · Binance 2025-26 · Delta 2025 · Delta 2026
(Delta with funding and the bot's real entry.) A promoted rule **passes** if, in at least 3 of the 4 sets, it beats the
plain ladder on R per unit of risk AND ends with a balance at least the ladder's AND its worst drop is no more than
5 points deeper than the ladder's.

## Consequence
A pass → the rule may be added to the rule A ladder spec, with these exact settings. No pass → not added.
