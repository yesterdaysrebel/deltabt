#!/bin/sh
# Re-run every study of 2026-11-15/16 quoted to the owner and diff against the saved output.
cd /src
run() { # name, saved file, command...
  n=$1; saved=$2; shift 2
  "$@" > out/review/$n.txt 2> out/review/$n.err
  if diff -q out/review/$n.txt "$saved" >/dev/null 2>&1; then echo "SAME   $n"; else echo "DIFF   $n ($(diff out/review/$n.txt "$saved" | grep -c '^[<>]') lines)"; fi
}
export PYTHONPATH=/src
run repeat_50        out/careful/repeat_50.txt        python scripts/careful_repeat_50.py
run run_winners_50   out/careful/run_winners_50.txt   python scripts/careful_run_winners_50.py
run small_75         out/careful/small_75.txt         python scripts/careful_small_75.py
run 500_month        out/careful/500_month.txt        python scripts/careful_500_month.py
run one_a_month      out/careful/one_a_month.txt      python scripts/careful_one_a_month.py
run 30_one_pct       out/careful/30_one_pct.txt       python scripts/careful_30_one_pct.py
run margin10_trail   out/fade_ladder_margin10_trail.txt python scripts/fade_ladder_margin10_trail.py
run ladder_be        out/fade_ladder_be.txt           python scripts/fade_ladder_be.py
run ladder_lev_be    out/fade_ladder_lev_be.txt       python scripts/fade_ladder_lev_be.py
run ladder_10x10     out/fade_ladder_10x10.txt        python scripts/fade_ladder_lev_be.py --ten
run one_pct          out/careful/one_pct.txt          python scripts/careful_one_pct.py
run big_one          out/careful/big_one.txt          python scripts/careful_big_one.py
run goal_1000        out/careful/goal_1000.txt        python scripts/careful_goal_1000.py
run wide_account     out/careful/wide_account.txt     python scripts/careful_wide_account.py
run entry_grid       out/careful/entry_grid.txt       python scripts/careful_entry_grid.py
run quick_book       out/careful/quick_book.txt       python scripts/careful_quick_book.py
echo DONE
