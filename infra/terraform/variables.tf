variable "aws_region" {
  description = "Region for every resource. Prefer one close to the exchange."
  type        = string
  default     = "ap-south-1"
}

variable "environment" {
  description = "Environment name. Part of every resource name."
  type        = string
  default     = "paper"

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.environment))
    error_message = "environment must be lowercase alphanumeric with hyphens."
  }
}

variable "github_org" {
  description = "GitHub organisation or user."
  type        = string
}

variable "github_repo" {
  description = "Repository name."
  type        = string
  default     = "deltabt"
}

variable "github_owner_id" {
  description = <<-EOT
    Numeric GitHub owner id. GitHub's immutable OIDC subject pins the owner and
    repository to their numeric ids, which a rename cannot follow. Empty falls
    back to the legacy name-based subject alone. `gh api users/<owner> --jq .id`
  EOT
  type        = string
  default     = ""
}

variable "github_repo_id" {
  description = "Numeric GitHub repository id. `gh api repos/<o>/<r> --jq .id`"
  type        = string
  default     = ""
}

# --- compute ---------------------------------------------------------------

variable "instance_type" {
  description = <<-EOT
    EC2 size. Measured from the running container: ~450 MB RSS steady and
    under 5% CPU, so t4g.small (2 vCPU / 2 GB) has comfortable headroom.
    t4g.micro (1 GB) also runs it but leaves little room for the numba cache
    and a docker build.

    BACK TO t4g.small ON 2026-08-19, after a detour. AWS ran out of the whole
    t4g pool in ap-south-1a for over three hours that morning -- t4g.small and
    t4g.MEDIUM failed identically, so it was never about sizing -- and
    m6g.medium was taken purely because it had capacity. That was a workaround
    for an outage, not a decision about what this workload needs, and it cost
    roughly $10/month more for headroom nothing uses.

    The durable fix was a public subnet in a second AZ, not a bigger instance:
    see bot_subnet_index. With somewhere to fall back to, the cheap shape is
    the right default again.

    IT IS PINNED HERE RATHER THAN PASSED AS -var ON PURPOSE. A command-line
    override leaves the committed default disagreeing with reality, and the
    next apply from CI would "correct" it -- replacing the instance and ending
    the experiment as a side effect of a routine plan.
  EOT
  type        = string
  default     = "t4g.small"
}

variable "bot_subnet_index" {
  description = <<-EOT
    Which public subnet -- and therefore which availability zone -- the bot
    runs in. 0 is the first AZ, 1 the second.

    THIS IS THE CAPACITY ESCAPE HATCH. On 2026-08-19 AWS had no t4g capacity in
    the first AZ for over three hours and the bot stayed down, because one
    public subnet existed and a host cannot run in a private one without a NAT.
    Every RunInstances error said the same thing: capacity is available in
    another zone.

    Changing this REPLACES the instance, so it needs allow_instance_replacement
    true for that apply. It does not move the database: RDS is single-AZ with
    its own subnet group, and the bot reaches it across the VPC either way.

    NOW 1 (the second AZ). t4g.small was still unavailable in the first AZ five
    hours after the outage began -- StartInstances itself returned
    InsufficientInstanceCapacity -- and the same shape launched in the second
    in 14 seconds. Committed rather than passed as -var so a CI apply cannot
    quietly move the bot back into the zone that has no capacity.
  EOT
  type        = number
  default     = 1

  validation {
    condition     = var.bot_subnet_index >= 0 && var.bot_subnet_index <= 1
    error_message = "bot_subnet_index must be 0 or 1; only two public subnets exist."
  }
}

variable "allow_instance_replacement" {
  description = <<-EOT
    Clears EC2 termination protection so a later apply MAY replace a bot host.

    TRUE DURING A ROLLOUT ONLY. Set back to false once the replacement has
    happened; leaving it true means a stray plan can terminate a running
    experiment, which is the thing the protection exists to prevent.

    Sequence, and it genuinely needs two applies:
      1. set true, apply -- updates the live attribute, replaces nothing
      2. apply the user-data change -- the replacement now succeeds
      3. set false again

    Terraform does not update attributes on a resource it is replacing, so
    doing 1 and 2 together leaves the destroy still blocked.

    BACK TO FALSE on 2026-08-19. It was flipped true on 2026-08-17 to let a
    user-data change reach the hosts, and then left true through a rollout
    that failed -- so instance termination stayed enabled for two days,
    including through a four-hour outage where a stray apply could have
    destroyed the surviving bot. Flip it deliberately, roll, flip it back.
  EOT
  type        = bool
  default     = false
}

variable "max_hold_seconds" {
  description = <<-EOT
    Close a position that has been open this long, at market, whatever it is
    doing. 0 disables it, and 0 IS THE CURRENT CONFIGURED VALUE -- this
    variable exists so the value can reach the container at all, which it
    previously could not.

    b63e365 shipped the code for a 24-hour time stop and called itself "Apply
    1 of 2". Apply 2 never landed: DELTABOT_MAX_HOLD existed only in the
    settings override table, and appeared in neither user_data.sh.tftpl nor
    the -e list in run.sh, so max_hold_seconds was 0 in every container
    regardless of intent.

    NOW 86400 (24h), because the ATR arm this stack runs is specified with a
    24-hour time exit. It was 0 while the plumbing was staged out.

    IT IS PART OF RiskConfig, SO IT IS PART OF THE RISK HASH. The experiment
    running here records 0338a386c43d39a4: that config WITH the 24h hold,
    max_trades_per_day at 20, and max_daily_loss_pct DISABLED at 1.0. It was
    f9a34a4b27a35684 while the daily loss cap was 2%, and 89f939adcd0a8567
    before that while the trade cap was 6. The same config at hold 0 hashes
    differently again. So changing this value makes
    a running bot raise ConfigurationDrift and refuse to continue -- the
    fail-closed behaviour working, not a fault. Changing it STARTS A NEW
    EXPERIMENT and cannot be applied to one already running.
  EOT
  type        = number
  default     = 86400
}

# --- access ----------------------------------------------------------------

variable "ami_id" {
  description = <<-EOT
    The AMI the bots run on, PINNED.

    data.aws_ami.al2023 uses most_recent = true, so the moment Amazon
    publishes a new al2023-*-arm64 image the lookup moves and `ami` forces
    replacement of every instance. On 2026-08-19 that was already true --
    a plan with NO configuration change wanted to replace all three running
    bots (ami-00b0a08d4568c22e8 -> ami-066a2d1dff4d3bfa5), which would have
    ended three 30-day forward tests as a side effect of an unrelated apply.

    Empty string falls back to the most_recent lookup. Bump this deliberately
    when you intend to re-bootstrap, never by accident.
  EOT
  type        = string
  default     = "ami-00b0a08d4568c22e8"
}

variable "root_volume_gb" {
  description = "Root EBS size. Image ~1.2 GB plus capped logs."
  type        = number
  default     = 20
}

# --- database --------------------------------------------------------------

variable "db_instance_class" {
  description = "RDS size. The bot writes a few hundred rows a minute."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  description = "Measured ~40 MB/day at four symbols, so 20 GB is years."
  type        = number
  default     = 20
}

variable "db_backup_retention_days" {
  description = <<-EOT
    Automated backup retention. The experiment database IS the deliverable of
    a 30-day run, so retention must outlive the run itself.
  EOT
  type        = number
  default     = 35
}

variable "db_deletion_protection" {
  description = "Refuse to delete the database holding the experiment."
  type        = bool
  default     = true
}

# --- container -------------------------------------------------------------

variable "ecr_repository_name" {
  description = "ECR repository for the bot image."
  type        = string
  default     = "deltabt"
}

variable "bot_image_tag" {
  description = <<-EOT
    Image tag to run. ALWAYS an immutable tag -- the git SHA -- never
    "latest". A mutable tag makes "which code produced this dataset"
    unanswerable, which is the one question a forward test must answer.
  EOT
  type        = string
  default     = ""
}

variable "bot_symbols" {
  description = <<-EOT
    Frozen universe. Changing it changes the experiment identity.

    NARROWED TO THE FOUR SYMBOLS THE STRATEGY WAS MEASURED ON, 2026-08-25.

    The six listed before were inherited from the frozen 1m arm. The
    wpr_only@240m backtest that selected v5 ran on BTCUSD, ETHUSD, SOLUSD and
    XRPUSD -- so XRPUSD was measured and not deployed, while BEATUSD, BANKUSD
    and AKEUSD were deployed and never measured at this timeframe. Half the
    universe was carrying no evidence at all, and the +3.23% / 10.09% drawdown
    figure that justified the arm describes the four.

    BANKUSD AND AKEUSD CANNOT BE MEASURED AT 240m, so this is not a gap
    somebody could close by running a longer backtest. Both listed 2026-07-22.
    Thirty-four days is ~204 primary bars against a 145-bar warm-up, which
    leaves too few post-warm-up bars to estimate anything from.

    THE THIN SYMBOLS ALSO BREAK THE BAR SET, which matters more than the
    missing evidence. In v5's first twenty minutes live:

        BANKUSD  1m gap detected  missing=16
                 gap repair fetched 0 of 16 missing minutes
        AKEUSD, BEATUSD                 unrepairable gaps as well

    deltabt.strategy.resample_complete drops any 240m bucket missing more than
    24 of its 240 minutes, so on these instruments the strategy silently skips
    primary bars. app/monitoring/health.py already carries a note about the
    same illiquidity holding candles_fresh red across all three hosts on
    2026-08-15. Nothing in the backtest resembles this: all four symbols here
    have 588 days of dense 1m history.

    RESTORED TO SIX ON 2026-08-26, by instruction, to reproduce experiment
    H-WPR-1-PAPER-ATR-20260820-4 -- the run whose configuration was approved.
    That run traded BTCUSD, ETHUSD, SOLUSD, BEATUSD, BANKUSD and AKEUSD, and
    trading a different universe would make the new trades incomparable to the
    94 already recorded.

    THE OBJECTIONS ABOVE STILL HOLD and are not withdrawn. BEATUSD, BANKUSD and
    AKEUSD produce candle gaps that cannot be repaired from any endpoint. What
    changed is which run this is: on a 5m primary with a 10% stop cap those
    symbols behave differently than on the 240m arm with a 5% cap, where v5's
    report recorded every one of their setups refused for stop width. The
    narrowing above was correct for v5 and is being reversed for a different
    strategy, not overruled.

    DROPPED TO FIVE ON 2026-08-29 AND RESTORED THE SAME DAY. The drop gave as
    its reason the line above -- "Changing it changes the experiment identity"
    -- which is an argument for leaving a universe alone, not for removing a
    symbol from it. It also left this block describing six symbols and naming
    BANKUSD among them while the default listed five, so the file argued
    against itself for the length of one commit. The six are restored, and the
    comparability argument of 2026-08-26 is the operative one again.

    WHAT BANKUSD COSTS ON A 5m PRIMARY. resample_complete keeps a bucket with
    at least min_frac=0.9 of its minutes, so a 5m bar needs 4 of 5: one absent
    minute is tolerated, two drop the bar, and the 16-minute gap observed on
    2026-08-26 removes three or four consecutive bars outright. The filter
    drops rather than truncates, so the bars that survive are honest and the
    cost is a thin, intermittent sample -- read BANKUSD's per-symbol counts
    with that in mind, and do not read its absence from a window as a signal.

    CHANGING THIS REPLACES THE INSTANCE. It is interpolated into user_data and
    ec2.tf sets user_data_replace_on_change = true, so BOT_INSTANCE_ID_V5 must
    be updated afterwards and the image re-rolled. Cheap only while no forward
    test is bound; once an experiment is RUNNING it would end the run.

    That sentence is deliberately not written with the CLI verb in it. The
    paper-only scan in tests/live/test_deployment_safety.py greps every
    shipped and deployment file for the literal command that begins a run, so
    that no automation can contain one -- and it cannot tell a comment from an
    instruction. Prose that names the command fails the build, which is the
    check being cheap rather than the check being wrong.

    NARROWED TO BEATUSD ALONE, 2026-08-31, for the manual_scalp arm.

    The clone was measured as a PORTFOLIO -- one account, the way the bot
    actually runs -- not as seven independent accounts, and the two differ
    enormously because losses compound on shared capital:

        all 7 symbols   8,759 trades   -91.2%   93.0% drawdown   $877 left
        thin 3            911 trades   -14.3%   20.3% drawdown
        BEATUSD only      744 trades    -4.4%   16.9% drawdown

    Breadth does not diversify here, it multiplies the trade count, and every
    trade pays cost. BEATUSD is the only symbol with a positive GROSS edge
    (+0.017R) because its 449 bps median stop costs 0.027R where BTCUSD's
    96 bps costs 0.103R -- the same cost law that killed every arm before it.

    AKEUSD AND BANKUSD ADDED BY INSTRUCTION, 2026-08-31. The operator's own
    hand trading made +1,892 and +1,269 on them -- they are the two symbols
    where the discretion demonstrably worked -- and the universe is theirs to
    choose.

    THE OBJECTIONS BELOW STILL HOLD AND ARE NOT WITHDRAWN. The clone INVERTS
    the manual result on exactly these two:

        symbol     hand-traded      manual_scalp backtest
        AKEUSD        +1,892        -0.083R  (gross -0.059)
        BANKUSD       +1,269        -0.186R  (gross -0.162)
        BEATUSD         -825        -0.010R  (gross +0.017)

    Both are gross-NEGATIVE, which is evidence the encoded rule does not
    capture whatever the discretion was doing there. Both also carry only three
    weeks of cached 1m history (from 2026-07-22), so neither result can be
    checked against a longer sample, and 87 and 80 backtested trades is not a
    sample at all.

    THE PORTFOLIO COST OF ADDING THEM, measured rather than assumed:

        BEATUSD only      744 trades    -4.4%   16.9% drawdown   $9,560
        thin 3            911 trades   -14.3%   20.3% drawdown   $8,568

    Read that as the price of the breadth, not as a forecast: on a 7-month
    single-account run the wider universe loses three times as much.

    CHANGING THIS REPLACES THE INSTANCE and ends the running experiment.

  EOT
  type        = string

  # WIDENED TO ALL SEVEN BY INSTRUCTION, 2026-09-01, for manual_scalp_st.
  #
  # THE MEASUREMENT SAYS THIS IS WORSE AND IS NOT WITHDRAWN. Portfolio, one
  # account, ungated -- the honest column, because a gated run that halts
  # early reports a truncated sample as a result:
  #
  #     universe                 return    max DD    trades
  #     thin 3 (BEAT/AKE/BANK)   -8.24%     15.6%       746
  #     4 symbols ST helps      -49.55%     54.7%     2,275
  #     six (no XRP)            -55.11%     57.1%     2,331
  #     all 7                   -55.56%     57.0%     2,472
  #
  # It is not breadth that hurts, it is WHICH symbols breadth adds. BTC, ETH,
  # SOL and XRP carry 1R widths of 30-45 bps, so cost_r runs 0.10-0.12 against
  # 0.03-0.04 on the thin three, and they generate most of the trades. The
  # same cost law that killed every arm before this one.
  #
  # THE REASON IT WAS CHOSEN ANYWAY IS SOUND AND IS RECORDED HERE SO IT IS NOT
  # MISREAD LATER AS AN OVERSIGHT. Seven symbols produce roughly three times
  # the trade rate. This is a PAPER run whose product is evidence, not money:
  # the thin 3 needs ~4 days to reach 30 closed trades, all 7 needs ~1-2. The
  # operator chose a decisive sample over a survivable equity curve, which is
  # a legitimate trade in paper and would not be in production.
  #
  # Expect a large drawdown. It is forecast, not a fault. What would be a
  # fault is reading the resulting P&L as a verdict on manual_scalp_st rather
  # than on this universe.
  # ALL SEVEN, BY INSTRUCTION, 2026-09-01 -- CHOSEN TWICE WITH THE NUMBERS IN
  # VIEW, SO THIS IS A DECISION AND NOT AN OVERSIGHT.
  #
  # Under the operator's own rule (Supertrend agrees AND %R banded), measured
  # as a PORTFOLIO on one account, ungated -- the honest column, because a
  # gated run that halts early reports a truncated sample as a result:
  #
  #     universe   trades/day    return    max DD    PF
  #     thin 3         2.38      -6.56%     14.9%   0.95
  #     all 7          3.23     -60.43%     63.7%   0.82
  #
  # 1.36x the sample for 9x the drawdown. The objection was put twice and is
  # NOT withdrawn: it is not breadth that hurts, it is WHICH symbols breadth
  # adds. BTC, ETH, SOL and XRP carry 1R widths of 30-45 bps, so cost_r runs
  # 0.10-0.12 against 0.03-0.04 on the thin three, and they generate most of
  # the trades. Getting the entry rule right does not touch that: BTCUSD's 1R
  # ran 71 bps live on 2026-09-01, so cost_r was 0.22 -- a fifth of the risk
  # budget gone to fees before the trade did anything.
  #
  # WHAT WOULD BE A FAULT is reading the resulting P&L as a verdict on
  # manual_scalp_st_banded rather than on this universe. Expect a large
  # drawdown; it is forecast. What this run can honestly measure is whether
  # the ENTRY RULE behaves out of sample, and seven symbols reach a 30-trade
  # sample sooner than three.
  default = "BEATUSD,AKEUSD,BANKUSD,ETHUSD,SOLUSD,BTCUSD,XRPUSD"
}

# --- the two concurrent runs -----------------------------------------------

variable "stacks" {
  description = <<-EOT
    One entry per concurrently running experiment. Each gets its own EC2
    instance, its own database on the shared RDS instance, its own log group,
    deploy document, monitor document and alarms.

    TERRAFORM DOES NOT CREATE THESE DATABASES. It builds the RDS instance;
    there is no sub-resource for a database inside one. So a new entry here
    names a database that exists in this map and nowhere on the server, and the
    bot dies at first start on InvalidCatalogNameError -- which is exactly how
    v4 failed its first roll on 2026-08-20.

    Create it before the first deploy, from the stack's own host so the
    credentials never leave it:

        deploy/aws/create_stack_database.py

    Schema is NOT part of that. Repository.connect calls migrate(), which
    applies schema.sql as CREATE TABLE IF NOT EXISTS on every start, so the
    tables are the bot's business and a second copy outside would drift.

    A SEPARATE DATABASE PER STACK IS LOAD-BEARING, NOT TIDINESS. Four
    invariants make two experiments in one database impossible:
    ux_forward_test_running allows one RUNNING experiment; ux_positions_open
    _symbol allows one open position per symbol across the whole table;
    strategy_state stores risk state under the single key "risk_state"; and
    load_open_positions() takes no experiment filter, so each bot would
    recover the other's positions. Splitting the database resolves all four
    without touching code that today's audit findings were about.

    "v1" keeps the original database because that is where the open BTCUSD
    position and the run history live.
  EOT
  type = map(object({
    variant = string
    db_name = string
  }))
  default = {
    # v3 (the ATR arm) and v4 (the flip arm) WERE REMOVED FROM THIS MAP on
    # 2026-08-25, not renamed and not disabled in code.
    #
    # Both were stopped when the research programme closed and their instances
    # terminated; the teardown also removed their log groups, metric filters,
    # alarms, EIPs and SSM documents, while Terraform state still carried them.
    # An apply would therefore have RECREATED two arms nobody asked to restart
    # -- and a subsequent walk-forward found neither holds a stable sign out of
    # sample. Leaving them here would have cost ~$24/month to run strategies
    # already measured as null.
    #
    # Their DATABASES on the shared RDS instance are untouched: deltabt_v3 and
    # deltabt_v4 still hold every trade both arms took. Restoring either stack
    # is re-adding its line here; the database it names already exists, so it
    # would not hit the InvalidCatalogNameError that broke v4's first roll.

    # The %R candidate, added 2026-08-25. On a 240-MINUTE bar: Williams %R(140)
    # TURNING UP goes long, TURNING DOWN goes short. No Supertrend, no ADX, no
    # confirmation timeframe, a 2 x ATR(10) stop and a 2R target.
    #
    # THIS IS NOT AN OVERSOLD REVERSAL, though the levels make it look like
    # one. The rule is `wpr_rule = "variant_a"`:
    #
    #     long  :  %R > -80  AND  %R > %R[1]
    #     short :  %R < -20  AND  %R < %R[1]
    #
    # The bands OVERLAP across [-80, -20], which is 67% of bars, and inside it
    # both level tests pass so only the DIRECTION of %R decides. Measured on
    # BTCUSD, 78% of longs and 85% of shorts fire inside that overlap, at a
    # median %R of -46.7 and -60.1. The levels only veto the outer tails: long
    # is blocked below -80, short above -20.
    #
    # So it is a momentum-direction rule that EXCLUDES the extremes, and with
    # no trend filter it takes both sides freely -- on 2026-08-11 it fired
    # long, short, long, short on four consecutive bars. The earlier wording
    # here, "rising above -80", is literally true and reads as a cross up
    # through -80 out of oversold. That is the `cross_levels` variant, which
    # this is not.
    #
    # SELECTED BY MEASUREMENT, AND THE MEASUREMENT IS WEAK. Of seven cells
    # tracked across four out-of-sample blocks it is one of only two holding a
    # positive GROSS sign in all four -- but 2 of 7 is roughly what chance
    # produces (0.88 expected, p = 0.215), its gross decays -0.032 per block,
    # and its net is negative in the two most recent. Portfolio backtest over
    # 588 days on four symbols, live gates, six position slots: +3.23% with a
    # 10.09% maximum drawdown and a per-trade expectancy of +0.0165R whose
    # confidence interval spans zero.
    #
    # It runs to MEASURE, not to earn. At 0.87 trades/day a 30-day run is ~26
    # trades against that interval and will return UNDECIDED, which must be
    # written down before the first bar rather than argued about at the end.
    #
    # THE VARIANT IS A SPEC, NOT A HAND-WRITTEN ARM. "SPEC:wpr_only@240"
    # resolves through deltabt.catalog to the same StrategySpec the backtester
    # ran, so the thing deployed is the thing measured.
    # v5 (SPEC:wpr_only@240) and v6 (SPEC:trend_wide_stop@60) WERE REMOVED FROM
    # THIS MAP on 2026-08-26, by instruction, after one day.
    #
    # v5's experiment SPEC-WPR-240-PAPER-20260825 was stopped with a reason
    # rather than left to look like a crash. It reached 16 evaluations, 2 fills
    # and 0 closed trades -- no result, and its frozen rule in
    # docs/v5_stopping_rule.md had called for evaluation at 30 days. v6 never
    # registered an experiment at all.
    #
    # Their DATABASES are untouched: deltabt_v5 and deltabt_v6 are not
    # Terraform-managed and still hold every signal, order and fill, including
    # v5's two positions left deliberately OPEN because closing them from
    # outside would fabricate exits the strategy never produced.
    #
    # Their log groups, EIPs, alarms and SSM documents go with the instances.
    # Restoring either stack is re-adding its line here; the database it names
    # already exists.

    # THE ATR ARM, RESTORED 2026-08-26. Keeps the stack name "v3" and the
    # database deltabt_v3 -- the one that already holds every trade the
    # previous ATR run took, so the two runs sit in one place and are
    # comparable. That run was stopped when the research programme closed,
    # before reaching the n = 100 / 2026-09-04 stopping point its own frozen
    # rule set (docs/v3_stopping_rule.md).
    #
    # "V4" IS THE ATR ARM. The name is a registry artifact, not a typo:
    # app/config/variants.py maps {"V4", "ATR", "V4_ATR"} to
    # app.strategy.atr_arm.ATR_ARM, while the STACK is called v3. Renaming
    # either would destroy and recreate a database and a log group.
    #
    # 5m primary, 1m confirmation, 2 x ATR(10) stop, 2R target.
    #
    # THE EVIDENCE FOR THIS ARM IS BAD AND IS RECORDED HERE RATHER THAN
    # ARGUED AWAY. Its out-of-sample walk-forward is net-NEGATIVE in all four
    # blocks -- -0.274, -0.109, -0.100, -0.293 -- and gross-negative in two.
    # It is not run because the backtest supports it. It is run because it is
    # what was asked for, as a risk-managed paper account with every circuit
    # breaker enabled, rather than as a measurement.
    #
    # UNLIKE v5 AND v6 THIS HAS NO STOPPING RULE AND IS NOT AN EXPERIMENT
    # UNDER TEST. Gates are on, so its expectancy is censored and must not
    # later be read as an unbiased estimate of anything.
    # NAMED FOR WHAT IT RUNS, not for its position in a sequence. The old
    # "stack v3 runs variant V4" was a registry artifact that cost real
    # confusion; app/config/variants.py already accepts "ATR" as a spelling of
    # the same arm, so nothing in code had to change to fix it.
    #
    # Renaming was free here because the previous v3 instance and log group
    # were destroyed in the 2026-08-25 teardown. There is nothing to rename in
    # place -- this is a fresh create under a better name.
    #
    # deltabt_atr is a NEW database. The 94 trades from the four earlier ATR
    # runs stay untouched in deltabt_v3 as the record of the UNGATED version,
    # which this run is not comparable to on P&L.
    #
    # 2026-08-27: THE VARIANT MOVED FROM "ATR" TO THE BANDED SPEC. The stack,
    # the database, the EIP, the log group and every alarm are reused; only
    # what the bot evaluates changed. `ATR-5M-GATED-20260826-2` was stopped
    # deliberately at 16 closed trades with no open positions.
    #
    # WHAT THE BAND DOES. atr_arm's long gate is `%R > -80 AND rising`, a floor
    # with no ceiling, so %R = -4 -- price at the high of its 140-bar window --
    # is a valid long. The live run entered longs at -4.3, -6.9, -8.6, -11.8
    # and -12.9 carrying a 2xATR stop worth 0.2-0.5% of price. `banded` keeps
    # every other condition and refuses entries past the midpoint of the band:
    # long in (-80, -50), short in (-50, -20).
    #
    # THE BACKTEST DOES NOT SUPPORT THIS, AND THAT IS RECORDED RATHER THAN
    # SOFTENED. Over 13,330 trades the band is -0.0028R against atr_arm with
    # SE 0.0193 (t = -0.14), better in 11 of 30 cells -- indistinguishable from
    # no effect, point estimate the wrong sign. Replayed against the 15 trades
    # the live arm actually took it looked good (+187.41, refusing 8 of 15) but
    # that is +0.272R with SE 0.915, t = +0.30, and it refused the day's two
    # best trades. It is run because it was asked for, not because it measured.
    #
    # `SPEC:` resolves through deltabt.catalog, so the thing deployed is the
    # thing the sweep ran -- no second implementation to keep in step.
    # 2026-08-29: BACK TO THE FROZEN atr_arm FOR AN UNGATED OBSERVATION RUN,
    # experiment ATR-5M-UN-GATED-PAPER-20260829-1. The stack, the database,
    # the EIP, the log group and every alarm are reused; only what the bot
    # evaluates changes. deltabt_atr is NOT recreated -- it holds open
    # positions from BANDED-5M-GATED-20260827-1 which recover() will adopt.
    #
    # "ATR" and "V4" are the same arm (app/config/variants.py), hash
    # 8a564836b862ea74, unchanged by this edit.
    #
    # THE BACKTEST STILL DOES NOT SUPPORT THIS ARM and nothing below is
    # withdrawn: net-negative in all four out-of-sample walk-forward blocks,
    # and the 2026-08-28 audit closed the family as ATR FAMILY DEAD. This run
    # is an implementation/accounting observation, not a measurement of edge,
    # and its P&L must not be read as evidence either way.
    # 2026-08-31: A NEW DATABASE, AND THE REASON IS THREE OPEN POSITIONS.
    #
    # ATR-5M-UN-GATED-PAPER-20260829-1 was stopped holding SOLUSD, BTCUSD and
    # ETHUSD. `forward-test stop` leaves open positions alone on purpose --
    # closing them would fabricate exits the strategy never produced -- and
    # manual_scalp trades BEATUSD/AKEUSD/BANKUSD, so recover() refused to start:
    #
    #     refusing to become ready: open position in SOLUSD, which is not in
    #     the configured universe
    #
    # That refusal is correct: a bot must not run while holding a position it
    # has no cost model or price feed for. Splitting the database resolves it
    # without touching those rows. deltabt_atr keeps the ATR run's history and
    # its three positions exactly as they were left, which is the honest record
    # of an experiment that ended holding them -- the same reasoning the note
    # above gives for why "v1" kept the original database.
    #
    # REMEMBER create_stack_database.py. It is not run by user_data; the
    # database must be created from the host before the first deploy, or the
    # bot fails to connect.
    # 2026-09-01: SPEC:manual_scalp_st@5, AND A NEW DATABASE AGAIN.
    #
    # manual_scalp gates on %R alone. It was built that way partly on a FALSE
    # reading: the manual-trade analysis reported 61% of the operator's entries
    # as counter-Supertrend, computed with `direction > 0` as bullish. Pine
    # returns direction -1 for an UPTREND (deltabt/rulecore.py:142), so the
    # column was inverted and the real figure is 62.4% ALIGNED. Corrected:
    #
    #     clean manual trades   n     win     mean R
    #     ALIGNED with ST      96   50.0%   -0.0455
    #     COUNTER to ST        62   50.0%   -0.1451
    #
    # Supertrend carried information and the arm was built without it. The
    # live run agrees on a sample too small to lean on: all 4 winners were
    # aligned, all 3 losers counter.
    #
    # ACROSS 6,559 BACKTESTED TRADES THE EFFECT IS ~ZERO (-0.0013R
    # trade-weighted), so this is a correction of a mistaken premise, not a
    # discovered edge. Four symbols improve, three worsen.
    #
    # THE NEW DATABASE IS NOT COSMETIC. MANUAL-SCALP-5M-PAPER-20260831-4 is
    # RUNNING and holds open BEATUSD and AKEUSD positions. `forward-test stop`
    # leaves open positions alone on purpose, and they would load into the new
    # arm holding two of six slots while bound to a dead experiment -- the
    # same failure that produced the 2026-08-31 split above, and the same
    # cleanup that cost an hour this morning. deltabt_manual keeps the
    # manual_scalp record and its open positions exactly as they were left.
    #
    # REMEMBER create_stack_database.sh. It is not run by user_data; the
    # database must be created from the host before the first deploy.
    # 2026-09-01, SECOND CHANGE OF THE DAY: SPEC:manual_scalp_st_banded@5.
    #
    # THE OPERATOR SAID PLAINLY THAT THE ARM WAS NOT RUNNING THEIR SYSTEM, AND
    # THEY WERE RIGHT THREE TIMES OVER. Their rule, as finally stated: "if wpr
    # is banded and supertrend agrees I take trades", %R length 140, Supertrend
    # 10/2.0, no ADX, no DI, 1R target. Every arm before this one got some part
    # of that wrong:
    #
    #   manual_scalp       %R variant_a alone -- a floor with no ceiling, so it
    #                      bought at %R -9 and sold at %R -93.
    #   manual_scalp_st    added Supertrend but kept variant_a, and opened four
    #                      shorts in ONE bar at %R -90 to -94, price a hair off
    #                      the leg low. Tested against the operator's rule, the
    #                      two agreed on NOTHING: 6 signals taken, 6 refused.
    #
    # `banded` bounds WHERE in the range an entry may happen. It is the piece
    # that was missing, and the objection raised three separate times.
    #
    # THREE THINGS THE OPERATOR ASKED FOR ARE DELIBERATELY NOT HERE, each
    # because it was measured and each because it costs money on the thin 3:
    #
    #     1m confirmation ON      -6.56% -> -13.46%   win 50% -> 44%
    #     max hold cut to 1h      -6.56% -> -18.38%
    #     tighter stop with it    -18.38% -> -34.41% at 2xATR
    #
    # The short hold is not wrong in itself; it is incompatible with a 4xATR
    # stop, because a 1R target on a stop that wide takes hours to reach. And
    # it cannot be rescued by tightening the stop: cost_r = round_trip/stop_pct,
    # so halving the stop doubles the cost per R. Best of a 15-cell grid was
    # 4xATR at 24h, which is what is configured.
    #
    # A NEW DATABASE, deltabt_stb, FOR THE THIRD TIME AND THE SAME REASON.
    # deltabt_st holds six open positions from the seven-symbol run, four of
    # them in ETHUSD/SOLUSD/BTCUSD/XRPUSD. Narrowing the universe would make
    # recover() refuse to start -- "open position in SOLUSD, which is not in
    # the configured universe" -- which is correct behaviour, not a bug.
    #
    # REMEMBER create_stack_database.sh. user_data does not run it.
    atr = { variant = "SPEC:manual_scalp_st_banded@5", db_name = "deltabt_stb" }
  }
}

variable "max_open_positions" {
  description = "Concurrent positions per experiment. Part of the risk hash."
  type        = number
  default     = 6
}

variable "max_drawdown_pct" {
  description = <<-EOT
    Peak-to-trough halt. 1.0 disables it: equity would have to reach zero.

    RE-ENABLED 2026-08-26 at the code default of 0.10, by instruction, for the
    v3 run. It had been disabled on 2026-08-14 for the measurement runs, on the
    grounds that a halt CENSORS the sample rather than improving it.

    That reasoning has not changed; the GOAL has. v3 is not a measurement run.
    It is left running as a risk-managed paper account, so bounding the
    drawdown is the point and the biased expectancy is accepted.

    THIS HALT IS TERMINAL AND LATCHES. app/risk/engine.py stops trading for
    good on breach and persists the flag; `forward-test resume --yes` is the
    only way out, and it rebases the peak so the run does not immediately
    re-halt. Anyone leaving this running unattended should know a 10% drawdown
    ends it silently but for the ERROR line and the daily report.
  EOT
  type        = number
  default     = 1.0
}

variable "max_daily_loss_pct" {
  description = <<-EOT
    Daily loss circuit breaker, as a fraction of start-of-day equity. 1.0
    disables it.

    THIS VARIABLE IS NEW, NOT A CHANGED DEFAULT. app/config/settings.py has
    always read DELTABOT_MAX_DAILY_LOSS, but user_data.sh.tftpl never set it,
    so the container fell back to the code default of 1.0 -- disabled -- and no
    amount of editing Terraform could turn the gate on. "All gates up" was not
    expressible before this.

    0.02 is the value that was in force before it was disabled on 2026-08-20.
  EOT
  type        = number
  default     = 1.0
}

variable "max_consecutive_losses" {
  description = <<-EOT
    Daily circuit breaker; the streak resets on the UTC day roll. 0 disables
    the gate. Note that the risk engine must SKIP the check at 0 rather than
    compare against it, since a fresh state already satisfies `losses >= 0`.

    RE-ENABLED 2026-08-26 at the code default of 3, by instruction. Unlike the
    drawdown halt this is a DAILY breaker: it clears at the UTC day roll rather
    than latching.
  EOT
  type        = number
  default     = 0
}

variable "admin_cidrs" {
  description = <<-EOT
    Optional inbound CIDRs. DEFAULT IS EMPTY AND SHOULD STAY EMPTY: access is
    via SSM Session Manager, which needs no inbound rule at all. Populating
    this opens SSH and is only justified if SSM is genuinely unavailable.
  EOT
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 90
}

variable "alarm_email" {
  description = "Optional address for alarm notifications. Empty disables SNS."
  type        = string
  default     = ""
}

variable "minimum_rr" {
  description = <<-EOT
    Reward/risk floor. A setup whose target is closer than this multiple of the
    stop is REFUSED by app/risk/engine.py before sizing.

    IT MUST TRACK THE ARM'S target_r, AND NOTHING CHECKED THAT UNTIL NOW.
    manual_scalp takes profit at 1R -- that is the whole finding it encodes,
    recovered from 165 hand-placed trades whose winners cluster at 0.5-1.5R.
    Against the 2.0 default every one of its setups computes rr = 1.00 and is
    refused, so the bot would boot, pass /readyz, report healthy, evaluate
    bars, and approve NOTHING, forever.

    This gate is LIVE-ONLY. deltabt/engine.py has no equivalent, so no backtest
    could ever surface the conflict; it was caught because forward-test
    preflight prints the risk config beside the strategy.

    Changing this moves risk_hash, so EXPECTED_RISK_HASH must be updated with
    it or the daily report calls a correct deployment drifted every morning.
  EOT
  type        = number
  default     = 1.0
}

variable "cooldown_after_trade_seconds" {
  description = <<-EOT
    Global post-trade cooldown, in seconds. 0 disables it.

    NEW 2026-08-29. app/config/settings.py has read DELTABOT_COOLDOWN_AFTER_TRADE
    since the same day, but user_data never wrote it, so the container fell back
    to the code default of 900 and no amount of editing this file could turn it
    off. That is the identical failure DELTABOT_MAX_DAILY_LOSS had: the code
    shipped, the delivery did not.

    BOTH COOLDOWNS ARE GLOBAL ACROSS SYMBOLS, NOT PER SYMBOL. Leaving them on
    makes the recorded sample whatever fires EARLIEST rather than a fair draw
    from the signal population, and that bias is invisible in the results it
    produces. 0 for an observation run whose purpose is an uncensored sample.

    Part of the risk hash: changing it makes a running bot refuse to continue
    its experiment rather than silently trade a different configuration.
  EOT
  type        = number
  default     = 0
}

variable "cooldown_after_loss_seconds" {
  description = <<-EOT
    Global post-loss cooldown, in seconds. 0 disables it. See
    cooldown_after_trade_seconds -- same delivery gap, same censoring argument,
    and at 3600 it suppressed every symbol for an hour after any single loss.
  EOT
  type        = number
  default     = 0
}
