# leakage — openai/gpt-5.6-terra-pro

1. **DEV is both the free-search dataset and the dataset used for a nominal hypothesis-test gate.**  
   - **Line:** §6 S2: “*pattern pass + model pass on DEV*”; §6 S4: “*run candidate on DEV (in-sample)* … *p ≤ 0.05; day-clustered t ≥ 2*.”  
   - **Hole:** The search is explicitly free on DEV—structure findings, clustering, model features, horizons, confidence cutoffs, controls, and up to two rules are selected there—then S4 assigns a conventional p-value/t-statistic to that same data. Those statistics are invalid as evidence after this adaptive search.  
   - **Fix:** Label S4 as exploratory/model-selection only, with no inferential p-value claims. Freeze a deterministic selection rule before looking at OLD—for example, a fixed search space and a fixed ranking metric selecting exactly one candidate—and make S5 the first confirmatory test. Alternatively split DEV into discovery and internal validation periods, with all transformations/model choices fit only in discovery.

2. **The “up to two candidates” cap does not specify how candidates are selected from the much larger search.**  
   - **Line:** §6 S2: “*up to two candidates written as rules*”; §3: “*lets a model search that structure freely*”; §3a: “*Candidates = the model's most confident picks as real trades*.”  
   - **Hole:** There is no precommitted definition of the candidate universe, model configurations, feature variants, horizons, threshold grid, or rule for choosing the two finalists. “Most confident” is especially ambiguous: model confidence can be calibrated, thresholded, filtered, and evaluated in many ways. This permits selecting the best-looking two after examining DEV outcomes. A register maintained only once something is called a “candidate” will not capture discarded near-candidates/configurations.  
   - **Fix:** Before S2, commit a search protocol: permitted data/features, all model/hyperparameter/threshold combinations, clustering settings, candidate-construction algorithm, and deterministic ranking/selection procedure. Log every evaluated configuration and every generated rule, including discarded ones, with a run ID and DEV metrics. If two candidates reach OLD, allocate confirmatory error across them or designate one primary candidate.

3. **OLD is described as untouched, but the supplied record establishes that it has already been repeatedly read.**  
   - **Line:** §6 S5: “*confirm on OLD, rules frozen*” and “*untouched window*”; §7: “*OLD untouched until S5*.”  
   - **Conflicting record:** The standing context reports many DEV/OLD results already observed, e.g. “*Stage 2 … 2024-06..2025-12*,” “*direction held in both windows*,” “*flow-only … DEV / OLD*,” and many other OLD results.  
   - **Hole:** “OLD” cannot be treated as an untouched, independent confirmation sample at programme level if its outcomes have already informed researchers’ beliefs, feature choices, mechanisms, exclusions, or proposed searches. Even if a particular candidate was not formally run on OLD, the window is not blind to the research process.  
   - **Fix:** Retire OLD as a confirmatory dataset for this programme, or narrowly document a quarantined subset whose outcomes and relevant data were never accessed. Use a newly obtained, access-controlled holdout instead. At minimum, create an immutable audit of all prior OLD access and explicitly exclude any candidate/feature family informed by it.

4. **The proposed final holdout cannot reproduce the Delta strategy or its information set.**  
   - **Line:** §6 S7: “*final hold-out (Binance 2019–2024 for perp candidates on majors)*”; §3a model inputs include “*settlement clocks, cross-venue gap, OI and positioning, flow, funding, listing age, own recent results*.”  
   - **Hole:** A Binance-only 2019–24 dataset cannot test a Delta-perp rule using Delta prices/marks, Delta funding and settlement mechanics, Delta listing age, Delta–Binance gaps, Delta OI/positioning, or Delta execution/costs. Removing those inputs for S7 changes the strategy after selection; imputing them would use unavailable information. It is therefore not a same-rule final holdout.  
   - **Fix:** Use a held-out Delta period with the full required Delta and Binance inputs, or pre-register a separate Binance-only transportability study with Binance-only features and Binance-appropriate execution assumptions. Do not call the latter final confirmation of a Delta strategy.

5. **The final-holdout provenance and blindness are not established.**  
   - **Line:** §4: “*Binance daily prices 2019–2024, majors (final hold-out) | no | fetch (minutes)*”; §6 S7 uses those data as the final holdout.  
   - **Hole:** “No” local copy does not establish that the data, their broad historical behavior, or prior analyses were never seen. A public archive available for minutes is easily accessible and may have been used in prior work. Moreover, S7 is historical data fetched only after a candidate survives, rather than a data asset cryptographically quarantined before discovery begins.  
   - **Fix:** Fetch and hash the full final-holdout dataset before discovery; place it under access control or encrypt it with a key held by the owner/reviewer; commit the retrieval script, manifest, date boundaries, and access log. State whether anyone has previously analyzed it. If prior exposure exists, it is not a final holdout.

6. **The option programme has known prior evidence on the very hypothesis family being proposed.**  
   - **Line:** §3b: “*232 cached expiries with max-pain and OI, never studied*”; §6 S9: “*options search … same S1–S7*.”  
   - **Conflicting record:** Standing context: “*Delta's option surface was measured efficient (no variance premium; IV tracks RV at 0.99).*”  
   - **Hole:** The document asserts that cached expiries were “never studied,” but the context says the Delta option surface has already been measured for efficiency. That is prior outcome knowledge in the options/surface family. It may affect which inconsistencies are pursued and invalidate any claim that the options DEV/OLD split is cleanly exploratory/confirmatory.  
   - **Fix:** Reconcile and inventory all previous options analyses, data extracts, plots, and summaries. Quarantine or exclude previously viewed expiries/features from confirmation. Treat the existing surface-efficiency result as prior exploration and use newly collected forward data, or a demonstrably unviewed historical partition, for confirmation.

7. **Current and incomplete instrument coverage creates survivorship and availability bias.**  
   - **Line:** §4: “*Delta 1m … 32 coins*”; “*Delta listings … 177 perps*”; standing facts: “*231 perpetuals*.”  
   - **Hole:** The discovery universe is not defined as an as-of-date historical universe. Thirty-two stored coins and 177 listing records are subsets of 231 current perpetuals, with no explanation of omitted, delisted, unavailable, renamed, or short-lived contracts. Searching only symbols that have retained usable history can favor survivors and can distort listing-age, liquidity, funding, and cross-venue effects.  
   - **Fix:** Commit an instrument master with symbol IDs, launch time, delisting/suspension time, rename mappings, and eligibility determined strictly as of each decision time. Include delisted/failed contracts where data exist, or explicitly limit the claim to the pre-defined covered universe and prohibit generalization to all Delta perps.

8. **“Later block” listing data is an undefined selection mechanism.**  
   - **Line:** §4: “*Delta listings, first 45 days + later block, 177 perps*.”  
   - **Hole:** “Later block” does not state how its dates were selected. If later observations are retained only for listings that survived, remained liquid, or were convenient to retrieve, both the listing study and any rule derived from it are survivor-selected.  
   - **Fix:** Define the later-block sampling rule before analysis—for example, days 46–90 after every listing, conditional only on the contract being listed at that time—and record missingness/delisting explicitly. Do not choose later dates based on post-listing performance, liquidity, or data availability.

9. **As-of availability and revision controls are missing for several proposed predictors.**  
   - **Line:** §7 inputs: “*computed only from data closed before the decision instant*”; §3a includes “*funding, listing age, own recent results*,” while §3b includes OI, max-pain, IV, skew, and term structure.  
   - **Hole:** A candle being “closed” is not enough. Funding values, OI/positioning snapshots, option OI/max-pain calculations, marks, and exchange candles may be published late or revised. Historical API retrieval can expose finalized values that were not available at the trading timestamp. “Own recent results” can also leak if it includes trades not fully resolved at the decision instant or uses revised fills.  
   - **Fix:** For every field, pre-register an availability timestamp/lag and use only the latest value demonstrably published before the decision. Preserve raw retrieval timestamps/versioned snapshots where possible. Lag funding/OI/options fields conservatively; define “own recent results” as settled, timestamped trades known before the signal. Add a test that deliberately shifts each feature by its publication lag and fails if any future timestamp is consumed.

10. **Walk-forward language does not require refitting all learned preprocessing within each training fold.**  
    - **Line:** §7: “*monthly walk-forward; never scored on training months*”; §3a: “*minute-level paths … clustered*”; §5: “*LightGBM*” and “*clustering of normalised paths*.”  
    - **Hole:** It is possible to score only future months while fitting normalization constants, cluster centroids, feature imputers, target encodings, scalers, thresholds, and model calibration on all DEV data. That leaks future distributional information into earlier walk-forward predictions. The plan does not explicitly forbid it.  
    - **Fix:** Require an expanding or rolling pipeline in which every learned transformation—including path normalization parameters, clustering, imputation, feature selection, calibration, and thresholds—is fit only on data available before each prediction month. Persist fold-specific artifacts and verify their maximum source timestamp.

11. **Overlapping 3/5/10-day labels and trade outcomes are not addressed by the stated inference.**  
    - **Line:** §3a: “*predict each coin's 3 / 5 / 10-day move at every day*”; §6 S4: “*day-clustered t ≥ 2*.”  
    - **Hole:** Daily observations with 3-, 5-, and especially 10-day forward labels overlap heavily. One market move can appear in many labels and many candidate trades. Clustering only by close day may not account for dependence across overlapping holding periods, coins, or the one-position portfolio path. This can overstate t-statistics and p-values during candidate selection.  
    - **Fix:** Use non-overlapping evaluation cohorts, block/bootstrap inference with blocks at least as long as the maximum holding horizon, or portfolio-level return series with an appropriate HAC/block procedure. Pre-register one horizon or adjust across horizons.

12. **Multiple-testing control is only a register, not a control procedure.**  
    - **Line:** §7: “*multiple comparisons | one register of every candidate, pass or fail*”; §6 S4/S5: nominal “*p ≤ 0.05*.”  
    - **Hole:** A candidate register is valuable for auditability but does not correct for the many structure probes, cluster counts, features, model configurations
