# Forward recorders — operations

Two processes keep the volatility programme alive. Neither can be backfilled:
whatever they do not capture at the moment the exchange serves it is gone.

| unit | what it records | cadence |
|---|---|---|
| `deltabt-quote-recorder` | option quote surface (bid/ask/size, IV, greeks, OI) | 900 s |
| `deltabt-perp-recorder` | BTCUSD/ETHUSD perpetual quotes + 1 m OHLCV | 60 s |

They are only useful **together**. H-Vol-6 needs the option surface *and* the
hedge instrument at the same instant, so the readiness metric is the overlap
between them, not either series alone.

---

## Before you install: two ways to silently lose the dataset

**1. A different data root.** Both units set
`Environment=DELTABT_DATA=/var/lib/deltabt/data`. The recorders default to
`<checkout>/data` when the variable is unset, which is where the manually
launched processes have been writing since 2026-08-24. Starting a unit with a
different root does not fail — it begins an empty second dataset and leaves the
real one to rot, while both `systemctl status` and the audit look healthy.

Point `DELTABT_DATA` at the directory that already holds the history, or move
that directory to `/var/lib/deltabt/data` first. There is no merge afterwards
that recovers a missed snapshot.

**2. A second writer.** `append_partition` is read-modify-write over the whole
daily partition: two writers race on the day, not the row, and one silently
discards the other's snapshots. Both recorders now take an exclusive `flock`,
but **the processes running since 2026-08-24 predate the lock and do not hold
it.** Stop them before starting a unit:

```sh
pkill -f 'deltabt.data.quote_recorder'
pkill -f 'deltabt.data.perp_recorder'
sleep 2
```

## Install

```sh
sudo cp deltabt-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now deltabt-quote-recorder deltabt-perp-recorder
journalctl -u deltabt-quote-recorder -u deltabt-perp-recorder -f
```

Installation needs root. The services themselves run as the unprivileged
`deltabt` user, with `ProtectSystem=strict` and a single `ReadWritePaths`.

`Restart=on-failure`, not `always`: a clean `systemctl stop` is an operator
decision and must not be undone by the supervisor. `StartLimitBurst=5` in 600 s
stops a restart storm — a process dying at startup has a bad path, a missing
venv or a held lock, none of which retrying fixes.

## Daily check

```sh
python -m deltabt.data.backup --verify-only     # checksums vs manifests
python scripts/vol_data_audit.py                # coverage, gaps, readiness
```

The audit prints the six-month clock as five distinct numbers. Only the last
one gates H-Vol-6:

```
calendar days (options) / usable options days / calendar days (perp)
usable perp days / overlap days / HEDGEABLE overlap days
```

## Backup

No durable target is configured. `scripts/backup.sh` covers the paper-trading
Postgres database only, and the sole S3 bucket in `infra/terraform` is the
OpenTofu **state** bucket — not a market-data target.

```sh
python -m deltabt.data.backup --destination /mnt/vol              # dry run
python -m deltabt.data.backup --destination /mnt/vol --execute    # copies
```

Dry run is the default. Copies are verified by re-hashing the destination, not
the source. Raw partitions are copied before the manifests that describe them,
so a torn backup never claims to describe data it does not contain.

---

## MANUAL REBOOT TEST — NOT EXECUTED

Everything below the reboot itself is covered by the automated suite: SIGKILL
mid-write, restart, checkpoint recovery, duplicate-free resumption, manifest
checksum continuity, a deliberate outage left as a real gap, and recovered bars
labelled `fetched_ts` rather than passed off as live.

**The host reboot itself has NOT been performed.** This environment is a dev
container running both recorders and the interactive session; rebooting it
would terminate the very dataset the exercise protects. Run this on the
production host after installing the units, and record the result here.

```sh
# 1. before
systemctl is-enabled deltabt-quote-recorder deltabt-perp-recorder   # expect: enabled
python -m deltabt.data.backup --verify-only > /tmp/pre-reboot.json
python scripts/vol_data_audit.py | tee /tmp/pre-reboot-audit.txt

# 2. reboot
sudo reboot

# 3. after — expect both active, without manual intervention
systemctl is-active deltabt-quote-recorder deltabt-perp-recorder
journalctl -u deltabt-perp-recorder --since "-10 min" | head -20

# 4. integrity: sealed partitions must still match their manifests
python -m deltabt.data.backup --verify-only

# 5. continuity: the downtime must appear as a GAP, never as filled minutes
python scripts/vol_data_audit.py
```

Pass requires all of:

- both units `active` after boot with no manual start
- checksum integrity `OK` on every sealed partition
- no duplicate rows under the dedup key
- no `.tmp` residue under `$DELTABT_DATA`
- the reboot window visible as missing minutes in the audit, **not** filled
- `hedgeable_usable_day_count` continues from its pre-reboot value

Do not record this as PASS until it has actually been run.
