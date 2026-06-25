"""Fixpoint-политика D1 (DESIGN-v2 §5.2)."""

from __future__ import annotations

from dataclasses import dataclass, field

from scripts.spikeB.marginals import measure
from scripts.spikeB.solve import SolveResult, solve


def _signature(res: SolveResult, opt_slots: list[str]) -> tuple:
    return tuple(
        tuple(sorted((a.group, a.type) for a in res.chosen.get(s, [])))
        for s in opt_slots
    )


def _avg_marg(m1: dict[str, float], m2: dict[str, float]) -> dict[str, float]:
    keys = set(m1) | set(m2)
    return {k: (m1.get(k, 0.0) + m2.get(k, 0.0)) / 2.0 for k in keys}


@dataclass
class FixpointStep:
    iter: int
    status: str
    dps: float
    signature: tuple
    action: str


@dataclass
class FixpointResult:
    dps: float
    overrides: dict[str, str]
    result: SolveResult
    history: list[FixpointStep] = field(default_factory=list)
    d1_applied: bool = False
    last_marg: dict[str, float] = field(default_factory=dict)


def fixpoint_d1(eng, build, opt_slots, pools, cfg, prefer, iters: int = 3,
                initial_overrides: dict[str, str] | None = None) -> FixpointResult:
    overrides = initial_overrides or {s: build.item_for_slot(s).stripped() for s in opt_slots}
    best_dps, best_ov, best_res = 0.0, overrides, SolveResult({}, {}, 0.0, "NONE")
    prev_sig: tuple | None = None
    prev_marg: dict[str, float] | None = None
    history: list[FixpointStep] = []
    d1_applied = False
    last_marg: dict[str, float] = {}

    for k in range(iters):
        _, marg = measure(eng, build, opt_slots, overrides, prefer)
        last_marg = marg

        use_marg = marg
        if prev_sig is not None and prev_marg is not None:
            res_probe = solve(build, opt_slots, pools, marg, cfg)
            if res_probe.status != "INFEASIBLE":
                sig_probe = _signature(res_probe, opt_slots)
                if sig_probe == prev_sig:
                    use_marg = _avg_marg(marg, prev_marg)
                    d1_applied = True
                    print(f"  iter {k}: D1 2-cycle -> averaging marginals")

        res = solve(build, opt_slots, pools, use_marg, cfg)
        if res.status == "INFEASIBLE":
            print(f"  iter {k}: INFEASIBLE")
            history.append(FixpointStep(k, "INFEASIBLE", 0.0, (), "infeasible"))
            break

        sig = _signature(res, opt_slots)
        dps = eng.dps(build.render(res.overrides))
        print(f"  iter {k}: status={res.status} PoB-DPS={dps:,.0f} "
              f"res(final)={ {r: round(res.res_final.get(r,0)) for r in ('fire_res','cold_res','light_res','chaos_res')} } "
              f"life+={res.life_value:.0f}")

        if dps > best_dps:
            best_dps, best_ov, best_res = dps, res.overrides, res

        improved = k == 0 or dps > history[-1].dps * 1.005
        action = "continue" if improved else "d1_inconclusive"
        history.append(FixpointStep(k, res.status, dps, sig, action))

        if not improved and k > 0:
            print(f"  iter {k}: D1 inconclusive -> best PoB-DPS={best_dps:,.0f}")
            break

        prev_sig, prev_marg = sig, marg
        overrides = res.overrides

    return FixpointResult(best_dps, best_ov, best_res, history, d1_applied, last_marg)
