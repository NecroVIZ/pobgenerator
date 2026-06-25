"""Оптимизация шмота (CP-SAT fixpoint + hybrid) — переиспользуемый блок для joint-спайка."""

from __future__ import annotations

from scripts.spikeB.fixpoint import fixpoint_d1
from scripts.spikeB.harness import Build
from scripts.spikeB.hybrid import multi_start
from scripts.spikeB.life_probe import measure_life_coefs
from scripts.spikeB.modpool import ModPool
from scripts.spikeB.solve import SolveConfig

DPS_KEYS = ("CombinedDPS", "TotalDPS", "FullDPS")
RES_KEYS = {"fire_res": "FireResist", "cold_res": "ColdResist",
            "light_res": "LightningResist", "chaos_res": "ChaosResist"}
LIFE_SLOTS = frozenset({"Helmet", "Body Armour", "Gloves", "Boots", "Belt",
                        "Amulet", "Ring 1", "Ring 2"})


def scaled_life_target(raw: float, opt_slots: list[str]) -> float:
    if raw <= 0:
        return 0.0
    n_life = sum(1 for s in opt_slots if s in LIFE_SLOTS)
    return raw * n_life / max(1, len(opt_slots))


def pick_dps_key(stats: dict) -> str:
    best, bv = "CombinedDPS", -1.0
    for k in DPS_KEYS:
        v = stats.get(k) or 0
        if v > bv:
            best, bv = k, v
    return best


def gear_config(eng, b_ref: Build, b_cur: Build, opt_slots: list[str],
                gear_ov: dict[str, str], prefer: str, life_frac: float = 0.6) -> SolveConfig:
    """Констрейнты шмота: цели с эталона, baseline с текущего состояния."""
    keys = list(RES_KEYS.values()) + ["Life"]
    ref = eng.stats(b_ref.xml, keys)
    cur = eng.stats(b_cur.render(gear_ov), keys)
    res_cap = {k: float(ref.get(v) or 0) for k, v in RES_KEYS.items()}
    res_base = {k: float(cur.get(v) or 0) for k, v in RES_KEYS.items()}
    ref_life = float(ref.get("Life") or 0)
    cur_life = float(cur.get("Life") or 0)
    life_target = scaled_life_target(max(0.0, (ref_life - cur_life) * life_frac), opt_slots)
    flat, inc = measure_life_coefs(eng, b_cur, opt_slots[0], gear_ov)
    return SolveConfig(
        res_cap=res_cap, res_baseline=res_base, life_base=cur_life or 3000.0,
        life_target=life_target, life_flat_coef=flat, life_inc_coef=inc,
    )


def optimize_gear(
    eng,
    meta,
    b_cur: Build,
    b_ref: Build,
    opt_slots: list[str],
    prefer: str,
    gear_ov: dict[str, str],
    *,
    life_frac: float = 0.6,
    fixpoint_iters: int = 2,
    bis_evals: int = 300,
    hybrid: bool = True,
) -> tuple[float, dict[str, str]]:
    """CP-SAT (+ optional hybrid) от текущего gear_ov. Возвращает (DPS, overrides)."""
    cfg = gear_config(eng, b_ref, b_cur, opt_slots, gear_ov, prefer, life_frac)
    pool = ModPool(meta)
    pools = {s: pool.for_base(b_cur.item_for_slot(s).base, b_cur.item_for_slot(s).item_level)
             for s in opt_slots}
    fp = fixpoint_d1(eng, b_cur, opt_slots, pools, cfg, prefer, fixpoint_iters,
                     initial_overrides=gear_ov)
    # если fixpoint не улучшил — оставляем вход; иначе берём CP-SAT
    cps_ov = fp.overrides if fp.dps > 0 else gear_ov
    cps_res = fp.result
    if not hybrid:
        return fp.dps if fp.dps > 0 else eng.dps(b_cur.render(gear_ov)), cps_ov
    hr = multi_start(eng, b_cur, opt_slots, pools, cfg, fp.last_marg,
                      cps_res.chosen if cps_res.chosen else {}, max_evals=bis_evals)
    return hr.dps, hr.overrides
