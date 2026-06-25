"""CP-SAT реализатор шмота (минимальный, спайк B).

Выбирает аффиксы редких слотов из пулов PoB, максимизируя ЛИНЕЙНЫЙ прокси урона
(Σ stat·marginal, маржиналы из самого PoB), при craftable-ограничениях:
  - на слот: ≤prefix_cap префиксов, ≤suffix_cap суффиксов;
  - одна группа аффиксов на предмет (взаимоисключение тиров);
  - элементальные резисты ≥ кап (cold/fire/light), chaos ≥ floor;
  - суммарная «жизнь» (flat + %·base) ≥ доля от того, что давал реальный шмот.
Легальность — забота солвера: PoB импортирует любой текст без проверки.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from scripts.spikeB.stats import OBJECTIVE


@dataclass
class SolveConfig:
    res_cap: dict[str, float]            # {"fire_res":85,...} цель для ele
    res_baseline: dict[str, float]       # резисты срезанного билда
    chaos_floor: float = 0.0
    life_base: float = 3000.0            # legacy: база для грубой оценки %life
    life_target: float = 0.0             # требуемый прирост жизни (PoB-эквивалент)
    life_flat_coef: float = 1.0          # ΔLife на +1 flat life (PoB-проба)
    life_inc_coef: float = 0.0           # ΔLife на +1% increased life (PoB-проба)


@dataclass
class SolveResult:
    overrides: dict[str, str]
    chosen: dict[str, list]              # slot -> list[Affix]
    proxy_dps_gain: float
    status: str
    res_final: dict[str, float] = field(default_factory=dict)
    life_value: float = 0.0


def _affix_value(affix, marg: dict[str, float]) -> float:
    return sum(affix.stats.get(k, 0.0) * marg.get(k, 0.0) for k in OBJECTIVE)


def _life_value(affix, life_base: float, flat_coef: float = 1.0, inc_coef: float = 0.0) -> float:
    flat = affix.stats.get("life", 0.0)
    inc = affix.stats.get("inc_life", 0.0)
    if inc_coef or flat_coef != 1.0:
        return flat * flat_coef + inc * inc_coef
    # fallback: грубая модель (legacy)
    return flat + inc * life_base / 100.0


def solve(build, opt_slots, pools: dict[str, list], marg: dict[str, float],
          cfg: SolveConfig) -> SolveResult:
    m = cp_model.CpModel()
    cand = []  # (slot, affix, x)
    for slot in opt_slots:
        for af in pools[slot]:
            x = m.NewBoolVar(f"{slot}:{af.group}:{af.type}")
            cand.append((slot, af, x))

    items = {slot: build.item_for_slot(slot) for slot in opt_slots}

    # caps по слоту
    for slot in opt_slots:
        it = items[slot]
        pref = [x for (s, a, x) in cand if s == slot and a.type == "Prefix"]
        suff = [x for (s, a, x) in cand if s == slot and a.type == "Suffix"]
        if pref:
            m.Add(sum(pref) <= it.prefix_cap)
        if suff:
            m.Add(sum(suff) <= it.suffix_cap)
        # группа-исключение
        groups: dict[str, list] = {}
        for (s, a, x) in cand:
            if s == slot:
                groups.setdefault(a.group, []).append(x)
        for g, xs in groups.items():
            if len(xs) > 1:
                m.Add(sum(xs) <= 1)

    SC = 1000  # масштаб для дробных резистов/жизни в int

    # резисты ele >= cap
    for r in ("fire_res", "cold_res", "light_res"):
        need = cfg.res_cap.get(r, 0) - cfg.res_baseline.get(r, 0)
        if need > 0:
            terms = [int(round(a.stats.get(r, 0) * SC)) * x for (s, a, x) in cand if a.stats.get(r, 0)]
            if terms:
                m.Add(sum(terms) >= int(round(need * SC)))
    # chaos >= cap (как ele)
    need_c = cfg.res_cap.get("chaos_res", 0) - cfg.res_baseline.get("chaos_res", 0)
    if need_c > 0:
        terms = [int(round(a.stats.get("chaos_res", 0) * SC)) * x for (s, a, x) in cand if a.stats.get("chaos_res", 0)]
        if terms:
            m.Add(sum(terms) >= int(round(need_c * SC)))
    # жизнь
    if cfg.life_target > 0:
        terms = [int(round(_life_value(a, cfg.life_base, cfg.life_flat_coef, cfg.life_inc_coef) * SC)) * x
                 for (s, a, x) in cand if _life_value(a, cfg.life_base, cfg.life_flat_coef, cfg.life_inc_coef)]
        if terms:
            m.Add(sum(terms) >= int(round(cfg.life_target * SC)))

    # цель: Σ proxy-value
    obj = [int(round(_affix_value(a, marg))) * x for (s, a, x) in cand]
    m.Maximize(sum(obj))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15
    st = solver.Solve(m)
    status = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
              cp_model.INFEASIBLE: "INFEASIBLE"}.get(st, str(st))

    chosen: dict[str, list] = {s: [] for s in opt_slots}
    res_final = dict(cfg.res_baseline)
    life_val = 0.0
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for (s, a, x) in cand:
            if solver.Value(x):
                chosen[s].append(a)
                for r in ("fire_res", "cold_res", "light_res", "chaos_res"):
                    res_final[r] = res_final.get(r, 0) + a.stats.get(r, 0)
                life_val += _life_value(a, cfg.life_base, cfg.life_flat_coef, cfg.life_inc_coef)

    overrides = {}
    for slot in opt_slots:
        lines = []
        for a in chosen[slot]:
            lines.extend(a.lines)
        overrides[slot] = items[slot].with_explicits(lines)

    return SolveResult(overrides=overrides, chosen=chosen,
                       proxy_dps_gain=solver.ObjectiveValue() if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else 0.0,
                       status=status, res_final=res_final, life_value=life_val)
