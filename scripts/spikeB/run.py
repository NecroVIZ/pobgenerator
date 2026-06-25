"""Спайк B end-to-end: фикс-скелет, CP-SAT по шмоту, fixpoint-перелинеаризация,
оценка в PoB vs реальный экспертный шмот и vs PoB-best-in-space (жадный поиск в PoB).

Метрики (decision-gate, см. SPIKES.md):
  - CP-SAT / PoB-best-in-space  — КАЧЕСТВО ПРОКСИ (риск №1): нашёл ли линейный прокси
    почти-оптимум в том же пространстве. ОСНОВНОЙ критерий: >=85% PASS, 60-85% погранично.
  - CP-SAT / реальный шмот       — справочно (разрыв из-за глубины крафта вне минимального пула).
"""

from __future__ import annotations

import argparse

from scripts.spikeB.engine import MetaEngine, PoolEngine
from scripts.spikeB.fixpoint import fixpoint_d1
from scripts.spikeB.harness import Build
from scripts.spikeB.hybrid import multi_start
from scripts.spikeB.life_probe import measure_life_coefs
from scripts.spikeB.modpool import ModPool
from scripts.spikeB.solve import SolveConfig

DPS_KEYS = ("CombinedDPS", "TotalDPS", "FullDPS")
RES_KEYS = {"fire_res": "FireResist", "cold_res": "ColdResist",
            "light_res": "LightningResist", "chaos_res": "ChaosResist"}


LIFE_SLOTS = frozenset({"Helmet", "Body Armour", "Gloves", "Boots", "Belt", "Amulet", "Ring 1", "Ring 2"})


def scaled_life_target(raw_target: float, opt_slots: list[str]) -> float:
    """Масштаб life_target: оружие не несёт life → доля цели пропорциональна life-слотам."""
    if raw_target <= 0:
        return 0.0
    n_life = sum(1 for s in opt_slots if s in LIFE_SLOTS)
    return raw_target * n_life / max(1, len(opt_slots))


def pick_dps_key(stats: dict) -> str:
    best, bv = "CombinedDPS", -1.0
    for k in DPS_KEYS:
        v = stats.get(k) or 0
        if v > bv:
            best, bv = k, v
    return best


def read_floors(eng, build, opt_slots, prefer):
    keys = list(RES_KEYS.values()) + ["Life"] + list(DPS_KEYS)
    stripped = {s: build.item_for_slot(s).stripped() for s in opt_slots}
    ref, base = eng.stats_batch([build.xml, build.render(stripped)], keys)
    res_cap = {k: float(ref.get(v) or 0) for k, v in RES_KEYS.items()}
    res_base = {k: float(base.get(v) or 0) for k, v in RES_KEYS.items()}
    return ref, base, stripped, res_cap, res_base


def fixpoint(eng, build, opt_slots, pools, cfg, prefer, iters=3):
    fp = fixpoint_d1(eng, build, opt_slots, pools, cfg, prefer, iters)
    return fp.dps, fp.overrides, fp.result, fp.last_marg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("build", nargs="?", default="builds/10.txt")
    ap.add_argument("--life-frac", type=float, default=0.6)
    ap.add_argument("--chaos-floor", type=float, default=0.0)
    ap.add_argument("--bis-evals", type=int, default=1200)
    ap.add_argument("--no-bis", action="store_true")
    ap.add_argument("--no-hybrid", action="store_true", help="только single-seed hill-climb (legacy)")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    b = Build.load(args.build)
    opt_slots = b.rare_core_slots()
    eng = PoolEngine(args.workers)
    meta = MetaEngine()

    refstats = eng.stats(b.xml, list(DPS_KEYS))
    prefer = pick_dps_key(refstats)
    eng.prefer = prefer
    print(f"build={args.build} opt_slots={opt_slots} dps_key={prefer} workers={eng.pool.n}")

    ref, base, stripped, res_cap, res_base = read_floors(eng, b, opt_slots, prefer)
    ref_dps = float(ref.get(prefer) or 0)
    base_dps = float(base.get(prefer) or 0)
    ref_life = float(ref.get("Life") or 0)
    base_life = float(base.get("Life") or 0)
    life_target = scaled_life_target(max(0.0, (ref_life - base_life) * args.life_frac), opt_slots)

    print(f"reference(real gear) DPS={ref_dps:,.0f} Life={ref_life:.0f}")
    print(f"stripped baseline    DPS={base_dps:,.0f} Life={base_life:.0f}")
    print(f"res_cap={res_cap} res_baseline={res_base}")
    print(f"life_target(+{args.life_frac:.0%})={life_target:.0f}")

    stripped = {s: b.item_for_slot(s).stripped() for s in opt_slots}
    flat_coef, inc_coef = measure_life_coefs(eng, b, opt_slots[0], stripped)
    print(f"life_coefs: flat={flat_coef:.2f}/pt  inc={inc_coef:.2f}/1%")

    cfg = SolveConfig(res_cap=res_cap, res_baseline=res_base, chaos_floor=args.chaos_floor,
                      life_base=base_life or 3000.0, life_target=life_target,
                      life_flat_coef=flat_coef, life_inc_coef=inc_coef)

    pool = ModPool(meta)
    pools = {s: pool.for_base(b.item_for_slot(s).base, b.item_for_slot(s).item_level) for s in opt_slots}

    print("\n== CP-SAT (linear proxy) + fixpoint (D1) ==")
    cps_dps, cps_ov, cps_res, last_marg = fixpoint(eng, b, opt_slots, pools, cfg, prefer)
    print(f"CP-SAT final PoB-DPS = {cps_dps:,.0f}")
    print("chosen affixes:")
    for s in opt_slots:
        names = [f"{a.type[0]}:{a.group}" for a in cps_res.chosen.get(s, [])]
        print(f"  {s:<12} {names}")

    bis_dps = None
    hybrid_stability = None
    if not args.no_bis:
        if args.no_hybrid:
            from scripts.spikeB.hybrid import hill_climb
            print("\n== PoB-best-in-space (single-seed hill-climb) ==")
            bis_dps, bis_ov, bis_evals, bis_chosen = hill_climb(
                eng, b, opt_slots, pools, cfg, cps_res.chosen, args.bis_evals)
            print(f"best-in-space PoB-DPS = {bis_dps:,.0f}  (evals={bis_evals})")
        else:
            print("\n== Hybrid (multi-start hill-climb: cpsat + greedy + random) ==")
            hr = multi_start(eng, b, opt_slots, pools, cfg, last_marg, cps_res.chosen,
                             max_evals=args.bis_evals)
            bis_dps, bis_ov, bis_chosen = hr.dps, hr.overrides, hr.chosen
            hybrid_stability = hr.stability
            print(f"hybrid PoB-DPS = {bis_dps:,.0f}  (evals={hr.evals})")
            print(f"seed DPS: { {k: f'{v:,.0f}' for k,v in hr.seed_dps.items()} }")
            print(f"seed stability (min/max) = {hybrid_stability:.1%}  "
                  f"{'OK' if hybrid_stability >= 0.8 else 'UNSTABLE'}")
        for s in opt_slots:
            print(f"  {s:<12} {[f'{a.type[0]}:{a.group}' for a in bis_chosen.get(s, [])]}")
        # верификация легальности/распознавания финального шмота самим PoB
        vr = meta.validate(b.render(bis_ov))
        item_warns = len(vr.get("item_problems", []))
        vstats = {k: meta.stat(v) for k, v in RES_KEYS.items()}
        vstats["Life"] = meta.stat("Life")
        print(f"  validator: gem_errors={len(vr.get('gem_errors', []))} "
              f"item_problems={item_warns} main_skill={vr.get('main_skill')!r}")
        if item_warns:
            print(f"  CAVEAT: {item_warns} modLine.extra — PoB молча не применяет часть модов")
        print(f"  bis resists/life: {vstats}")

    print("\n== VERDICT ==")
    print(f"CP-SAT / reference(real) = {cps_dps/ref_dps*100:.1f}%")
    if bis_dps:
        ratio = cps_dps / bis_dps * 100
        verdict = "PASS" if ratio >= 85 else ("BORDERLINE" if ratio >= 60 else "FAIL")
        print(f"CP-SAT / hybrid-best     = {ratio:.1f}%  -> {verdict} (proxy quality)")
        if hybrid_stability is not None and hybrid_stability < 0.8:
            print(f"HYBRID UNSTABLE: seed spread min/max={hybrid_stability:.1%} (<80%)")
    print(f"total PoB evals = {eng.evals}")
    eng.close()


if __name__ == "__main__":
    main()
