"""Гибридный оптимизатор: multi-start hill-climb от разных seed'ов.

Снимает риск локального застревания (ROUND2-FINDINGS §4): если разные seed'ы дают
сильно разный DPS (>20% разброс) — ядро ненадёжно на этом архетипе.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from scripts.spikeB.solve import SolveConfig, _affix_value, _life_value


@dataclass
class HybridResult:
    dps: float
    overrides: dict[str, str]
    chosen: dict[str, list]
    evals: int
    seeds: list[str]          # имена seed'ов
    seed_dps: dict[str, float]  # DPS после hill-climb от каждого seed
    stability: float            # min/max по seed_dps (1.0 = идеально стабильно)


def _feasible(ch, items, opt_slots, cfg, res_keys=("fire_res", "cold_res", "light_res", "chaos_res")):
    res = dict(cfg.res_baseline)
    life = 0.0
    for s in opt_slots:
        it = items[s]
        npre = sum(1 for a in ch[s] if a.type == "Prefix")
        nsuf = sum(1 for a in ch[s] if a.type == "Suffix")
        grps = [a.group for a in ch[s]]
        if npre > it.prefix_cap or nsuf > it.suffix_cap or len(grps) != len(set(grps)):
            return False
        for a in ch[s]:
            for r in res_keys:
                res[r] = res.get(r, 0) + a.stats.get(r, 0)
            life += _life_value(a, cfg.life_base, cfg.life_flat_coef, cfg.life_inc_coef)
    for r in ("fire_res", "cold_res", "light_res"):
        if res.get(r, 0) < cfg.res_cap.get(r, 0) - 1e-6:
            return False
    if res.get("chaos_res", 0) < cfg.res_cap.get("chaos_res", 0) - 1e-6:
            return False
    if life < cfg.life_target - 1e-6:
        return False
    return True


def _overrides_of(ch, items, opt_slots):
    return {s: items[s].with_explicits([l for a in ch[s] for l in a.lines]) for s in opt_slots}


def hill_climb(eng, build, opt_slots, pools, cfg, seed_chosen, max_evals=600):
    """Один проход hill-climb (вынесено из run.py для multi-start)."""
    items = {s: build.item_for_slot(s) for s in opt_slots}
    chosen = {s: list(seed_chosen.get(s, [])) for s in opt_slots}
    evals = 0

    def moves(ch):
        for s in opt_slots:
            grps = {a.group for a in ch[s]}
            npre = sum(1 for a in ch[s] if a.type == "Prefix")
            nsuf = sum(1 for a in ch[s] if a.type == "Suffix")
            it = items[s]
            for af in pools[s]:
                if af.group in grps:
                    continue
                if af.type == "Prefix" and npre < it.prefix_cap:
                    yield (s, None, af)
                elif af.type == "Suffix" and nsuf < it.suffix_cap:
                    yield (s, None, af)
            for old in list(ch[s]):
                for af in pools[s]:
                    if af.group == old.group or af.group in grps:
                        continue
                    if af.type == old.type:
                        yield (s, old, af)

    cur = eng.dps(build.render(_overrides_of(chosen, items, opt_slots)))
    evals += 1
    improved = True
    while improved and evals < max_evals:
        improved = False
        trials = []
        for (s, old, af) in moves(chosen):
            trial = {k: list(v) for k, v in chosen.items()}
            if old is not None:
                trial[s].remove(old)
            trial[s].append(af)
            if _feasible(trial, items, opt_slots, cfg):
                trials.append(trial)
            if len(trials) >= max_evals - evals:
                break
        if not trials:
            break
        dps_list = eng.dps_batch([build.render(_overrides_of(t, items, opt_slots)) for t in trials])
        evals += len(trials)
        best_gain, best_ch, best_dps = 0.0, None, cur
        for t, d in zip(trials, dps_list):
            if d - cur > best_gain:
                best_gain, best_ch, best_dps = d - cur, t, d
        if best_ch and best_gain > cur * 0.002:
            chosen, cur = best_ch, best_dps
            improved = True
    return cur, _overrides_of(chosen, items, opt_slots), evals, chosen


def greedy_seed(pools, opt_slots, marg, cfg, build):
    """Жадный seed: топ аффикс по proxy-value в каждый свободный слот."""
    items = {s: build.item_for_slot(s) for s in opt_slots}
    chosen: dict[str, list] = {s: [] for s in opt_slots}
    for s in opt_slots:
        it = items[s]
        grps: set[str] = set()
        npre = nsuf = 0
        ranked = sorted(pools[s], key=lambda a: _affix_value(a, marg), reverse=True)
        for af in ranked:
            if af.group in grps:
                continue
            if af.type == "Prefix" and npre < it.prefix_cap:
                trial = {k: list(v) for k, v in chosen.items()}
                trial[s].append(af)
                if _feasible(trial, items, opt_slots, cfg):
                    chosen[s].append(af)
                    grps.add(af.group)
                    npre += 1
            elif af.type == "Suffix" and nsuf < it.suffix_cap:
                trial = {k: list(v) for k, v in chosen.items()}
                trial[s].append(af)
                if _feasible(trial, items, opt_slots, cfg):
                    chosen[s].append(af)
                    grps.add(af.group)
                    nsuf += 1
    return chosen


def random_seed(pools, opt_slots, cfg, build, rng: random.Random):
    """Случайный выполнимый seed."""
    items = {s: build.item_for_slot(s) for s in opt_slots}
    chosen: dict[str, list] = {s: [] for s in opt_slots}
    for s in opt_slots:
        it = items[s]
        grps: set[str] = set()
        candidates = list(pools[s])
        rng.shuffle(candidates)
        npre = nsuf = 0
        for af in candidates:
            if af.group in grps:
                continue
            if af.type == "Prefix" and npre < it.prefix_cap:
                trial = {k: list(v) for k, v in chosen.items()}
                trial[s].append(af)
                if _feasible(trial, items, opt_slots, cfg):
                    chosen[s].append(af)
                    grps.add(af.group)
                    npre += 1
            elif af.type == "Suffix" and nsuf < it.suffix_cap:
                trial = {k: list(v) for k, v in chosen.items()}
                trial[s].append(af)
                if _feasible(trial, items, opt_slots, cfg):
                    chosen[s].append(af)
                    grps.add(af.group)
                    nsuf += 1
    return chosen


def multi_start(eng, build, opt_slots, pools, cfg, marg, cps_chosen, max_evals=400, n_random=1):
    """Multi-start hill-climb: CP-SAT seed + greedy + random(s). Берём лучший."""
    seeds: dict[str, dict] = {
        "cpsat": {s: list(cps_chosen.get(s, [])) for s in opt_slots},
        "greedy": greedy_seed(pools, opt_slots, marg, cfg, build),
    }
    rng = random.Random(42)
    for i in range(n_random):
        seeds[f"random{i}"] = random_seed(pools, opt_slots, cfg, build, rng)

    seed_dps: dict[str, float] = {}
    best_dps, best_ov, best_ch, total_evals = 0.0, {}, {}, 0
    per_seed = max(80, max_evals // len(seeds))
    for name, ch in seeds.items():
        dps, ov, ev, chosen = hill_climb(eng, build, opt_slots, pools, cfg, ch, per_seed)
        seed_dps[name] = dps
        total_evals += ev
        if dps > best_dps:
            best_dps, best_ov, best_ch = dps, ov, chosen

    vals = [v for v in seed_dps.values() if v > 0]
    stability = min(vals) / max(vals) if vals and max(vals) > 0 else 0.0
    return HybridResult(best_dps, best_ov, best_ch, total_evals,
                        list(seeds), seed_dps, stability)
