# Deploying DeltaBot to a VPS for the 30-day paper forward test

**Paper trading only.** The bot reads public market data and has no
order-placement capability. No exchange credentials are needed anywhere in this
document, and none should ever be added — the *absence* of the capability is
the safety boundary, not a flag.

The only secret involved is the PostgreSQL password.

---

## The decision you have to make first

**Managed PostgreSQL is strongly preferred and I could not provision it.**

The experiment database *is* the deliverable of a 30-day run. Everything else —
the image, the code, the config — is reproducible from the repository. The
database is not.

| | Managed PostgreSQL | Self-hosted on the VPS |
|---|---|---|
| Survives VPS loss | **Yes** | **No** — one disk failure loses bot *and* evidence |
| Backups | Provider snapshots, point-in-time restore | Only what `scripts/backup.sh` writes, **and only if it is off-box** |
| Restore drill | Provider console | Your `restore.sh` into a fresh instance |
| Cost | ~$15/mo entry tier | Included |
| Blast radius | Bot dies → data intact | Bot dies with the data |

**If you self-host it anyway, these become mandatory, not optional:**

1. The database lives on a **named Docker volume**, never in the container
   layer and never on an ephemeral disk.
2. `scripts/backup.sh` runs daily by cron **and ships the dump off the VPS**
   (object storage, or `scp` to another host). A backup on the same disk as the
   database is not a backup.
3. You run `scripts/restore.sh` into a scratch database **once, before the
   experiment starts**. An untested restore is a guess.

I did not create a temporary PostgreSQL and call it production. The compose file
in `deploy/vps/` points at an external `DATABASE_URL` precisely so the managed
option is the default path.

---

## VPS requirements

Measured from the running container, not guessed:

| Resource | Observed | Recommend |
|---|---|---|
| RAM | ~450 MB steady (numpy/pandas/numba + 4 symbol buffers) | **2 GB** |
| CPU | <5% steady; 4 evaluations/minute at ~2 ms each | **2 vCPU** |
| Disk (bot) | ~1.2 GB image + 200 MB capped logs | **20 GB** |
| Disk (DB, if self-hosted) | ~40 MB/day at 4 symbols → ~1.2 GB for 30 days | **+20 GB** |
| Network | A few hundred kB/min of websocket traffic | any |

Anything from a $6–12/month tier is sufficient. The bot is not compute-bound;
it is availability-bound.

**Required before deploying:**

```bash
timedatectl set-timezone UTC          # the bot stores UTC; the host should agree
timedatectl                            # confirm NTP is active and synchronised
docker --version && docker compose version
```

Clock: the bot uses **exchange timestamps** for every trading decision and the
wall clock only for staleness detection, so NTP drift cannot alter a signal.
It can still make `/healthz` wrong, so keep NTP on.

---

## Host hardening

```bash
# firewall: SSH only. The bot API is NOT exposed.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw enable

# SSH: keys only
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/'                /etc/ssh/sshd_config
systemctl restart ssh

# unattended security updates
apt install -y unattended-upgrades && dpkg-reconfigure -plow unattended-upgrades
```

**Public ports: exactly one — 22/tcp.**

The dashboard binds to `127.0.0.1` inside the compose file. Reach it from your
Mac over an SSH tunnel:

```bash
ssh -N -L 8000:127.0.0.1:8000 user@vps
# then open http://localhost:8000
```

No `cloudflared`, no public tunnel, no reverse proxy. If PostgreSQL is
self-hosted it must **not** publish a port at all — the bot reaches it over the
compose network.

---

## Deploy

```bash
git clone https://github.com/yesterdaysrebel/deltabt.git /opt/deltabt
cd /opt/deltabt
git checkout <the SHA you intend to run>

cat > .env <<'EOF'
GIT_SHA=<that same SHA>
GIT_DIRTY=0
DELTABOT_TAG=<short-sha>
DATABASE_URL=postgresql://user:pass@managed-host:5432/deltabt
EOF
chmod 600 .env          # never commit this; .gitignore already blocks .env

docker compose -f deploy/vps/docker-compose.yml build
docker compose -f deploy/vps/docker-compose.yml up -d
```

`GIT_SHA` is **required**: containers have no git, and preflight fails on an
unknown SHA because a result that cannot be tied to code is not reproducible.
It is baked into the image at build time.

### The database must be dedicated

Learned the hard way during the deployment rehearsal: a database that already
contains an experiment from a different commit makes the bot **refuse to
start** — correctly — and Docker will crash-loop it:

```
ERROR    configuration drift in experiment ...: git_sha: <old> -> <new>
CRITICAL CONFIGURATION DRIFT -- refusing to start
```

That is the fail-closed guard doing its job. Point the run at a **clean,
dedicated database**, not one shared with tests or a previous run.

---

## Verification before starting the experiment

```bash
docker compose -f deploy/vps/docker-compose.yml exec bot python -m app forward-test preflight
```

All 16 checks must PASS. Then run each of these and confirm the behaviour:

| Test | Command | Expect |
|---|---|---|
| Restart | `./scripts/restart.sh` | Same experiment, positions/cooldown/PnL recovered, no duplicates |
| Kill | `docker kill deltabot` | Restart policy brings it back; state recovered from PostgreSQL |
| Reboot | `reboot` | Container returns automatically; `./scripts/status.sh` healthy |
| Network | unplug/`ufw deny out` briefly | Feed reconnects with backoff, gaps detected and repaired, health goes unhealthy then green |
| Database | restart the DB | `database_writable` recovers; bot does **not** restart |

**The actual 24/7 acceptance test** — the one that matters:

1. Start the experiment on the VPS.
2. Close the SSH session. Shut the Mac down entirely.
3. Wait several hours.
4. Power the Mac back on, `ssh` in, run `./scripts/status.sh`.
5. Confirm uptime spans the gap, evaluations continued, and no restarts
   occurred that you did not cause.

---

## Operating it

```bash
./scripts/status.sh          # one screen: health, feed, risk, positions, experiment
./scripts/logs.sh            # follow everything
./scripts/logs.sh incident   # errors, criticals, quarantines, drift only
./scripts/logs.sh risk       # risk gates and rejections
./scripts/restart.sh         # clean restart; never fabricates exits
./scripts/backup.sh          # dump + verify + prune
```

Daily backup and daily report by cron:

```cron
17 0 * * *  cd /opt/deltabt && BACKUP_DIR=/var/backups/deltabt ./scripts/backup.sh >> /var/log/deltabt-backup.log 2>&1
30 0 * * *  cd /opt/deltabt && docker compose -f deploy/vps/docker-compose.yml exec -T bot \
              python -m app forward-test report --day "$(date -u -d yesterday +\%F)" >> /var/log/deltabt-daily.log 2>&1
```

**Backup facts to record before you start:** database host, backup destination
(must be off-box), retention (`BACKUP_KEEP`, default 30), and the date you last
rehearsed a restore.

---

## Alerting

The bot already records every condition worth alerting on as a structured
event. The minimum viable alert needs no extra service:

```cron
*/5 * * * *  curl -fsS --max-time 10 http://127.0.0.1:8000/healthz >/dev/null \
               || echo "deltabot unhealthy at $(date -u)" >> /var/log/deltabt-alerts.log
```

Conditions that appear as `ERROR`/`CRITICAL` in `./scripts/logs.sh incident`:
bot down, feed stale, database unavailable, experiment stopped, risk-limit
breach, quarantined fill, reconciliation failure, configuration drift.

External alerting (Telegram, PagerDuty, email) needs credentials for a third
party. **It is deliberately not required for launch** — the notification layer
is already abstracted behind `app/notifications/base.py`, so adding a provider
later is a contained change and does not touch the strategy.

---

## Logging

Docker's `json-file` driver is capped at **20 MB × 10 files ≈ 200 MB**. Without
that cap a 30-day run of structured logs fills the disk and takes the database
down with it.

Logs are JSON, one object per line, and carry a `logger` field that identifies
the component: `market_data`, `strategy`, `risk`, `execution`, `persistence`,
`monitoring`, `runtime`. `scripts/logs.sh` filters on those. No secrets are
logged — the AST safety scan fails the build if a credential identifier appears
anywhere in `app/`.

---

## Starting the experiment

Only after every verification above passes:

```bash
docker compose -f deploy/vps/docker-compose.yml exec bot \
  python -m app forward-test start --days 30
```

The experiment ID defaults to `H-WPR-1-PAPER-<UTCDATE>`. Override with
`--experiment-id` if you want the `-VPS-` marker. **`H-WPR-1-PAPER-20260813`
must not be reused** — it is stopped and immutable, retained as the artifact of
the run that found the `max_open_positions` race.

During the 30 days, change nothing: no parameters, no risk, no symbols, no
filters, no manual intervention in individual trades. If a technical failure
occurs, stop the experiment, fix it, and start a **new** experiment ID. Do not
silently patch and continue — a run whose code changed underneath it is two
experiments wearing one name.
