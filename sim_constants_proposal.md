# Player-Level Sim Constants Proposal

This document lays out every number I'd hard-code into the new player-level Monte Carlo sim, where the number came from, and what it controls. Every constant should land in one `_SIM_CONSTANTS` block at the top of the sim module so we can tune it in one place and calibrate later against a real prior season.

---

## 1. Sigma model for hitters

The core formula for a hitter's per-category standard deviation in one trial is:

```
sigma(stat) = mu(stat) × BASE_CV[stat] × PA_SCALE × CONFIDENCE_MULT
```

where `mu(stat)` is the FG Depth Charts projected rest-of-season total, and the three multipliers stack.

### BASE_CV — per-stat volatility for a full-season everyday player (~600 PA)

| Stat  | BASE_CV | Why |
|-------|---------|-----|
| R     | 0.15    | Depends on lineup context and games played; moderately sticky |
| HR    | 0.22    | HR/FB rate is noisy year-to-year; the biggest individual-stat swing |
| RBI   | 0.18    | Rate-stat dependent on runners on base, correlated with R |
| SB    | 0.30    | Most volatile counting stat — green light is discretionary |
| OBP   | 0.06    | Very sticky; K% and BB% regress slowly |

These are 3–4× the current team-level CVs because aggregating ~14 hitters at the team level smooths variance by roughly √14. Player-level CVs should look wider because the law of large numbers isn't yet in play.

### PA_SCALE — scales variance with playing time

```
PA_SCALE = sqrt(600 / max(PA_proj, 100))
```

A platoon bat projected for 300 PA gets `sqrt(2) ≈ 1.41×` wider sigma than a full-timer. A bench bat at 150 PA gets 2×. Capped at 100 PA to prevent prospect call-ups from exploding sigma to infinity.

### CONFIDENCE_MULT — rookie / veteran uncertainty

Derived from age and MLB track record:

| Bucket                               | Mult |
|--------------------------------------|------|
| Rookie (<200 career PA)              | 1.35 |
| Sophomore (200–500 PA)               | 1.20 |
| Established, age 24–30, 500+ PA      | 1.00 |
| Veteran, age 31–33                   | 1.05 |
| Old, age 34+                         | 1.15 |

Rookies have widest projection cones because the league hasn't adjusted to them. Older players have modest widening driven by decline-curve uncertainty, not by the projection itself being less trustworthy.

---

## 2. Sigma model for pitchers

Same structure, different constants. Formula:

```
sigma(stat) = mu(stat) × BASE_CV_PIT[stat, role] × IP_SCALE × CONFIDENCE_MULT
```

### BASE_CV_PIT — split by starter vs reliever because their volatility profiles are totally different

| Stat  | Starter | Reliever | Why |
|-------|---------|----------|-----|
| W     | 0.30    | 0.50     | Run support dominates; infamously random |
| K     | 0.12    | 0.18     | K-rate is sticky; volume scales with IP |
| SV    | —       | 0.40     | Role risk dominates; closers lose jobs |
| HLD   | —       | 0.40     | Same — setup roles are fluid |
| ERA   | 0.18    | 0.25     | Relievers on ~70 IP have massive small-sample noise |
| WHIP  | 0.12    | 0.18     | Slightly less noisy than ERA |

### IP_SCALE

```
IP_SCALE_sp = sqrt(180 / max(IP_proj, 40))   # starters normalized to 180 IP
IP_SCALE_rp = sqrt(65  / max(IP_proj, 20))   # relievers normalized to 65 IP
```

### CONFIDENCE_MULT

Same age buckets as hitters but using IP thresholds: <50 IP career = 1.35×, 50–150 IP = 1.20×, etc.

---

## 3. Within-player correlation (the latent-factor model)

Instead of a full covariance matrix (12×12 per player, impractical), we use a single latent "form factor" per player per trial. Each player draws one `z ~ N(0, 1)` representing "how good was his trial," and each stat samples as:

```
sampled[stat] = mu[stat] + sigma[stat] × (LAMBDA[stat] × z + sqrt(1 - LAMBDA[stat]²) × eps_stat)
```

where `eps_stat ~ N(0, 1)` independently per stat. `LAMBDA` is the per-stat loading on the shared form factor — high lambda means the stat moves with overall player form, low lambda means it's more independent.

### LAMBDA_HIT — hitter loadings on form factor

| Stat  | λ    | Rationale |
|-------|------|-----------|
| R     | 0.75 | Driven by getting on base and the guys behind you |
| HR    | 0.70 | Power correlates with overall quality of contact |
| RBI   | 0.75 | Same — good offensive season = more RBI |
| SB    | 0.30 | More independent — depends on legs and manager philosophy |
| OBP   | 0.60 | Driven by plate discipline, which is stable |

### LAMBDA_PIT — pitcher loadings

| Stat  | λ    | Rationale |
|-------|------|-----------|
| W     | 0.60 | Partially about form, partially about luck |
| K     | 0.55 | K-rate is skill but volume depends on IP which fluctuates |
| ERA   | 0.85 | Huge loading — ERA and overall form are nearly synonymous |
| WHIP  | 0.85 | Same; strongly correlated with ERA |
| SV    | 0.40 | Lower — role and opportunity matter as much as quality |
| HLD   | 0.40 | Same |

This gets us correlated R/HR/RBI for hitters and correlated ERA/WHIP/K for pitchers without the expense of a full covariance matrix.

---

## 4. Ratio stats — distribution choice

OBP, ERA, and WHIP should use log-normal draws instead of Gaussian to prevent nonsensical tails. For ERA specifically:

```
log_era_sample ~ N(log(mu_era), sigma_log)
sigma_log ≈ BASE_CV_PIT[ERA]   # CV ≈ sigma_log for small CVs
```

OBP uses a Beta distribution with mean `mu_OBP` and concentration tuned to hit the target CV, bounded [0, 1]. WHIP uses log-normal like ERA.

These are small fidelity fixes that prevent outlier trials from producing negative ERAs or OBPs above 1.

---

## 5. Injury model

Every player has a per-week probability of transitioning from healthy to IL. If they land on IL mid-trial, we zero out some fraction of their remaining projection for that trial.

### Base weekly IL probability (derived from published position injury rates)

| Position                 | P(IL per week) | Implied season-loss % |
|--------------------------|----------------|-----------------------|
| Starting pitcher         | 0.013          | ~22%                  |
| Relief pitcher           | 0.009          | ~15%                  |
| Catcher                  | 0.010          | ~17%                  |
| Middle infield (2B/SS)   | 0.008          | ~14%                  |
| Corner infield (1B/3B)   | 0.007          | ~12%                  |
| Outfield                 | 0.007          | ~12%                  |
| DH                       | 0.006          | ~10%                  |

These numbers are grounded in Baseball Prospectus and FanGraphs injury-report aggregates from the last several seasons. The 22% figure for starters is driven mostly by elbow/shoulder injuries and has been remarkably stable since 2015.

### Age multiplier

| Age      | Mult |
|----------|------|
| <27      | 0.85 |
| 27–31    | 1.00 |
| 32–34    | 1.30 |
| 35+      | 1.70 |

### Injury-history multiplier (if we can pull it)

| History                        | Mult |
|--------------------------------|------|
| No prior IL (last 2 seasons)   | 1.00 |
| One IL stint prior year        | 1.60 |
| Multiple IL stints prior year  | 2.00 |

Injury history is the single strongest individual predictor. If we don't have it in the FG export, we'd pull from a second source — it's worth the effort. Without it, we'd skip this multiplier and accept that we're modeling position × age averages.

### Severity when a player lands on IL

```
remaining_fraction_lost ~ Uniform(0.25, 0.70)
```

A quarter-season loss for a minor strain up to two-thirds for a serious injury. This is intentionally a wide uniform because we don't have severity data and real injuries are bimodal (minor/major) in ways a single distribution can't capture cleanly.

---

## 6. Closer role-change model

Applied only to projected closers (identified by SV projection > 15). Each trial, we draw a "loses role" event:

### Base probability: 30% per season

Grounded in the observed ~30–40% attrition rate for opening-day closers across recent seasons.

### Modifiers (additive, capped at 65%)

| Condition                                        | Added |
|--------------------------------------------------|-------|
| Projected ERA > 3.80                             | +10%  |
| Projected ERA > 4.20                             | +20% (replaces +10%) |
| Age < 25 or > 34                                 | +5%   |
| Setup reliever has better projected ERA         | +8%   |
| First-year closer (<10 career SV)                | +8%   |
| Hard cap                                         | 65%   |

### Consequence when role is lost

When a closer loses his job mid-trial, 70% of his remaining projected saves transfer to the next-best reliever on the same team (by projected ERA). The former closer keeps holds (demoted to 8th inning) and keeps his K/ERA/WHIP mostly intact.

Non-closer role changes are intentionally NOT modeled explicitly — we absorb that uncertainty into the `CONFIDENCE_MULT` for flagged players (rookies, platoon bats, anyone with a projected PA below some threshold).

---

## 7. Starter rotation-spot changes

A lighter-weight version of role change for starters. Pitchers projected for <25 starts have a 20% chance per season of losing their rotation spot. When it fires, half their remaining IP transfers to the next starter up (waiver-wire quality), which drags their team's ratio stats slightly and cuts their K/W.

This is a small effect but it prevents the sim from treating every back-end starter as a rock-solid 30-start guy.

---

## 8. The full constants block (draft)

```python
_SIM_CONSTANTS = {
    # ─── Hitter sigmas ─────────────────────────────────────
    "hit_base_cv": {
        "R": 0.15, "HR": 0.22, "RBI": 0.18, "SB": 0.30, "OBP": 0.06,
    },
    "hit_pa_norm": 600,
    "hit_pa_floor": 100,

    # ─── Pitcher sigmas ────────────────────────────────────
    "pit_base_cv_sp": {
        "W": 0.30, "K": 0.12, "ERA": 0.18, "WHIP": 0.12,
    },
    "pit_base_cv_rp": {
        "W": 0.50, "K": 0.18, "SV": 0.40, "HLD": 0.40,
        "ERA": 0.25, "WHIP": 0.18,
    },
    "pit_ip_norm_sp": 180,
    "pit_ip_norm_rp": 65,
    "pit_ip_floor_sp": 40,
    "pit_ip_floor_rp": 20,

    # ─── Confidence multipliers (both H and P) ─────────────
    "conf_rookie": 1.35,
    "conf_sophomore": 1.20,
    "conf_established": 1.00,
    "conf_vet31_33": 1.05,
    "conf_old34plus": 1.15,

    # ─── Within-player correlation loadings ────────────────
    "lambda_hit": {
        "R": 0.75, "HR": 0.70, "RBI": 0.75, "SB": 0.30, "OBP": 0.60,
    },
    "lambda_pit": {
        "W": 0.60, "K": 0.55, "ERA": 0.85, "WHIP": 0.85,
        "SV": 0.40, "HLD": 0.40,
    },

    # ─── Injury model ──────────────────────────────────────
    "injury_weekly_p": {
        "SP": 0.013, "RP": 0.009, "C": 0.010,
        "2B": 0.008, "SS": 0.008, "1B": 0.007, "3B": 0.007,
        "OF": 0.007, "DH": 0.006,
    },
    "injury_age_mult": [  # (max_age, mult)
        (26, 0.85), (31, 1.00), (34, 1.30), (999, 1.70),
    ],
    "injury_severity_lo": 0.25,
    "injury_severity_hi": 0.70,

    # ─── Closer role change ────────────────────────────────
    "closer_base_p": 0.30,
    "closer_sv_threshold": 15,
    "closer_era_bump_380": 0.10,
    "closer_era_bump_420": 0.20,
    "closer_age_bump": 0.05,
    "closer_setup_better_bump": 0.08,
    "closer_first_year_bump": 0.08,
    "closer_p_cap": 0.65,
    "closer_saves_transferred": 0.70,

    # ─── Starter rotation change ───────────────────────────
    "starter_at_risk_gs_threshold": 25,
    "starter_loss_p": 0.20,
    "starter_ip_transferred": 0.50,
}
```

---

## 9. Calibration plan

Every constant above is a starting point, not a final answer. Once v1 is implemented, we should:

1. **Smoke-test against current season projections** — make sure finish-probability distributions look reasonable (Team Alex doesn't have 95% chance of winning or losing).
2. **Backtest against 2025** — take 2025 opening-day projections, run the sim, compare the finish-probability distribution against actual 2025 standings. Check calibration: across all teams and all finish positions, did the predicted probabilities match observed frequencies?
3. **Tune per-stat CVs** if calibration is off. The most likely culprit is SV and HLD volatility — those are hardest to get right without the injury/role-change components also being dialed in.
4. **Sensitivity test the trade machine** — run a couple of real trades and see if the predicted finish-probability deltas feel right versus your intuition. If a star-for-star trade produces a 0.01 delta, sigmas are too wide. If a junk-for-star trade produces a 3.0 delta, they're too tight.

The calibration harness should live in a separate test file and re-run on demand so we can iterate on constants without touching the sim code.

---

## 10. What this doesn't yet model

For honesty, here's what v1 still won't capture and where we'd go next:

- **H2H matchup structure.** We're still doing roto as a proxy. The fix is a full H2H weekly-matchup sim as discussed, which is a follow-up project.
- **Cross-player correlation on the same MLB team.** Two Yankees hitters' weekly R totals are correlated because they face the same pitching staffs. This is a nice-to-have but probably worth <3% of realism gain.
- **Schedule effects.** Facing the Rockies at Coors vs the Dodgers at Dodger Stadium matters, and the sim doesn't know about it.
- **Non-closer role changes** (platoon flips, lineup shuffles). Absorbed into sigma widening rather than modeled explicitly.
- **Weather and park factors.** Already baked into the FG projections, so not our problem.

---

## Decision points for you

Before I start coding, three things worth your input:

First, do the BASE_CV numbers look reasonable or do you want any dialed up/down based on your experience? The ones I'm least confident in are SB (might be even higher than 0.30 in practice) and W for starters (could easily be 0.35).

Second, do you want me to pull injury history from a second source so we can use the history multiplier, or skip that for v1 and add it later?

Third, the closer role-change model is relatively aggressive (up to 65% probability of losing the role). Is that consistent with your experience of how often your closers implode in a season, or should I pull the cap down to ~50%?

Once you've given me a sign-off or adjustments on these, I'll start the refactor.
