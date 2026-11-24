"""
P4 - Monte Carlo disruption simulation
--------------------------------------
Scenario: online checkout / platform outage during peak trading (Black Friday week).
Questions:
  1. What is the distribution of revenue loss and customer impact?
  2. How likely is the outage to exceed our IMPACT TOLERANCE (max tolerable disruption)?
  3. How much does the continuity plan (failover + manual workaround + comms) reduce the risk,
     and which assumptions matter most?

ALL INPUTS ARE ILLUSTRATIVE ASSUMPTIONS for a fictional retailer (not any real company's figures).
Every assumption is in ASSUMPTIONS below so a reviewer can challenge and change it.
"""
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = "."
N = 100_000
rng = np.random.default_rng(2024)

ASSUMPTIONS = dict(
    peak_day_online_revenue_m = 12.0,          # GBP m on the peak day (assumption)
    peak_hours_weight = [0.2,0.1,0.05,0.05,0.05,0.1,0.2,0.4,0.6,0.8,0.9,1.0,1.0,0.9,0.9,0.9,1.0,1.1,1.3,1.4,1.4,1.2,0.8,0.4], # hourly demand shape
    outage_start_hour_probs = "weighted by demand shape (outage is equally likely at any time, impact depends on when)",
    outage_median_h = 2.5, outage_sigma = 0.8,   # lognormal duration, median 2.5h, fat right tail
    abandon_baseline = 0.65,                     # share of customers who do NOT return when checkout is down (no plan)
    abandon_with_plan = 0.40,                    # with status page, queue page, comms, retry incentives
    gross_margin = 0.32,
    recovery_h_baseline = (0.5, 2.0),            # extra hours (uniform) to detect + decide before fixing, no plan
    recovery_h_with_plan = (0.1, 0.5),           # monitoring + decision rights pre-agreed
    failover_success_p = 0.75,                   # chance warm standby works when invoked
    failover_switch_h = 0.5,                     # time to switch if it works
    manual_capacity_share = 0.15,                # share of orders that can be taken via phone/assisted channel during outage (plan only)
    compensation_per_affected_order = 2.0,       # GBP goodwill
    avg_order_value = 85.0,
    impact_tolerance_h = 4.0,                    # max tolerable outage duration agreed with leadership (assumption)
    impact_tolerance_loss_m = 3.0,               # max tolerable GBP m revenue loss (assumption)
)
A = ASSUMPTIONS
w = np.array(A["peak_hours_weight"]); w = w / w.sum()

def simulate(plan: bool, n=N, **override):
    a = {**A, **override}
    start = rng.uniform(0, 24, n)
    raw_dur = rng.lognormal(np.log(a["outage_median_h"]), a["outage_sigma"], n)
    detect = rng.uniform(*(a["recovery_h_with_plan"] if plan else a["recovery_h_baseline"]), n)
    dur = raw_dur + detect
    if plan:                                    # warm failover can cap the duration if it works
        ok = rng.random(n) < a["failover_success_p"]
        dur = np.where(ok, np.minimum(dur, detect + a["failover_switch_h"]), dur)
    # lost revenue = demand share inside the outage window x daily revenue
    hr = np.arange(24)
    cdf = np.concatenate([[0], np.cumsum(w)])
    def cum(x):                                   # piecewise-linear cumulative demand at hour x (wraps after 24h)
        x = np.asarray(x); full = np.floor(x / 24); r = x - full * 24
        i = np.minimum(r.astype(int), 23); frac = r - i
        return full + cdf[i] + frac * (cdf[i + 1] - cdf[i])
    share = cum(start + dur) - cum(start)
    demand_in_window = share * a["peak_day_online_revenue_m"]
    abandon = a["abandon_with_plan"] if plan else a["abandon_baseline"]
    abandon = np.clip(rng.normal(abandon, 0.06, n), 0.05, 0.95)
    manual = a["manual_capacity_share"] if plan else 0.0
    lost_rev = demand_in_window * abandon * (1 - manual)
    orders_hit = demand_in_window * 1e6 / a["avg_order_value"]
    comp = orders_hit * a["compensation_per_affected_order"] / 1e6 * (0.5 if plan else 1.0)
    total = lost_rev + comp
    return pd.DataFrame(dict(duration_h=dur, lost_revenue_m=lost_rev, total_loss_m=total,
                             lost_margin_m=lost_rev * a["gross_margin"] + comp, orders_affected=orders_hit))

base, plan = simulate(False), simulate(True)

def stats(d, name):
    return dict(scenario=name, mean_loss_m=d.total_loss_m.mean(), p50_loss_m=d.total_loss_m.median(),
                p95_loss_m=d.total_loss_m.quantile(.95), p99_loss_m=d.total_loss_m.quantile(.99),
                mean_duration_h=d.duration_h.mean(), p95_duration_h=d.duration_h.quantile(.95),
                prob_exceed_time_tolerance=(d.duration_h > A["impact_tolerance_h"]).mean(),
                prob_exceed_loss_tolerance=(d.total_loss_m > A["impact_tolerance_loss_m"]).mean())
summary = pd.DataFrame([stats(base, "No continuity plan"), stats(plan, "With continuity plan")]).set_index("scenario").T
summary.round(3).to_csv(f"{OUT}/simulation_summary.csv")

# ------------------------------------------------------------ sensitivity (one-at-a-time, plan scenario, mean loss)
sens = []
base_mean = plan.total_loss_m.mean()
for k, (lo, hi) in {"outage_median_h": (1.5, 4.0), "outage_sigma": (0.5, 1.1), "abandon_with_plan": (0.25, 0.60),
                    "failover_success_p": (0.40, 0.95), "manual_capacity_share": (0.0, 0.30),
                    "peak_day_online_revenue_m": (8.0, 16.0)}.items():
    r = []
    for v in (lo, hi):
        rng = np.random.default_rng(99)
        r.append(simulate(True, n=30_000, **{k: v}).total_loss_m.mean())
    sens.append((k, r[0], r[1]))
sens = pd.DataFrame(sens, columns=["assumption", "mean_loss_at_low", "mean_loss_at_high"])
sens["swing"] = (sens.mean_loss_at_high - sens.mean_loss_at_low).abs()
sens = sens.sort_values("swing")
sens.to_csv(f"{OUT}/sensitivity.csv", index=False)

# ------------------------------------------------------------ charts
fig, ax = plt.subplots(1, 3, figsize=(19, 5.5))
bins = np.linspace(0, base.total_loss_m.quantile(.995), 60)
ax[0].hist(base.total_loss_m, bins=bins, alpha=.6, color="#c62828", label="No plan")
ax[0].hist(plan.total_loss_m, bins=bins, alpha=.6, color="#1f3b73", label="With plan")
ax[0].axvline(A["impact_tolerance_loss_m"], color="k", ls="--"); ax[0].text(A["impact_tolerance_loss_m"] + .05, ax[0].get_ylim()[1] * .9, "loss tolerance", fontsize=9)
for d, c in ((base, "#c62828"), (plan, "#1f3b73")): ax[0].axvline(d.total_loss_m.quantile(.95), color=c, ls=":")
ax[0].set_xlabel("Total loss (GBP m)"); ax[0].set_title("Loss distribution (dotted = 95th percentile)"); ax[0].legend()

bins = np.linspace(0, 14, 56)
ax[1].hist(base.duration_h, bins=bins, alpha=.6, color="#c62828", label="No plan")
ax[1].hist(plan.duration_h, bins=bins, alpha=.6, color="#1f3b73", label="With plan")
ax[1].axvline(A["impact_tolerance_h"], color="k", ls="--"); ax[1].text(A["impact_tolerance_h"] + .1, ax[1].get_ylim()[1] * .9, "time tolerance (4h)", fontsize=9)
ax[1].set_xlabel("Outage duration (hours)"); ax[1].set_title("Duration vs impact tolerance"); ax[1].legend()

ax[2].barh(sens.assumption, sens.mean_loss_at_high - base_mean, left=base_mean, color="#e07b00", alpha=.8)
ax[2].barh(sens.assumption, sens.mean_loss_at_low - base_mean, left=base_mean, color="#2e7d32", alpha=.8)
ax[2].axvline(base_mean, color="k"); ax[2].set_title("Sensitivity of mean loss (with plan)\n green = low value, orange = high value"); ax[2].set_xlabel("Mean loss GBP m")
plt.tight_layout(); plt.savefig(f"{OUT}/montecarlo_results.png", dpi=140); plt.close()

pd.set_option("display.float_format", lambda x: f"{x:,.3f}")
print(summary.to_string())
print("\nSensitivity (largest swing last):\n", sens.round(3).to_string(index=False))
print(f"\nExpected loss reduction from plan: {1 - plan.total_loss_m.mean() / base.total_loss_m.mean():.0%}; "
      f"95th pct loss reduction: {1 - plan.total_loss_m.quantile(.95) / base.total_loss_m.quantile(.95):.0%}")
