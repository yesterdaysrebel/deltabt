"""Run 4 final rules with the owner's manual habit added: stop for the rest of the month once the month is up $2,000
(owner 2026-11-15: "I would have stopped trading when 2000 target was hit"). Same account sim as scripts/run4_account.py
(fill-first, the other order cancelled, 2-stops hard stop), A and B checked against each other.

    PYTHONPATH=. python scripts/run4_cap2000.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import run4_account as RA  # noqa: E402

for cap in (None, 2000.0):
    RA.CAP = cap
    print("=" * 30, "NO monthly profit stop" if cap is None else f"STOP FOR THE MONTH AT +${cap:,.0f}", "=" * 30)
    RA.main()
    print()
