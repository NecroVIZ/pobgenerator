"""ML-v0 inference + eval vs hillclimb on holdout (gate: DPS% primary)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from poebuildgen import pobcode
from poebuildgen.headless import PobHeadless
from poebuildgen.pool import WorkerPool, _dps_from
from scripts.ml_v0.axis import build_features_from_xml, feature_vector, load_xml
from scripts.spikeC.tree_build import compare, dijkstra_to_targets, load_tree_graph, main_tree_notables, split_main_ascend
from scripts.spikeC.tree_marginals import DPS_KEYS, hillclimb_tree_build, pick_dps_key
from scripts.spikeC.tree_xml import parse_mastery_effects, render_tree_nodes

_REPO = Path(__file__).resolve().parents[2]
OUT_DIR = _REPO / "corpus" / "ml_v0"
GOLD_EVAL = ["builds/2.txt", "builds/4.txt", "builds/6.txt", "builds/8.txt", "builds/10.txt", "builds/11.txt"]


def _load_model():
    meta = json.loads((OUT_DIR / "train_meta.json").read_text(encoding="utf-8"))
    path = OUT_DIR / meta["model_path"]
    if meta["backend"] == "catboost":
        from catboost import CatBoostClassifier
        model = CatBoostClassifier()
        model.load_model(str(path))
        return model, meta, "catboost"
    import joblib
    pack = joblib.load(path)
    return pack, meta, "sklearn"


def _score_notables(model, meta, backend: str, feats: dict, candidate_nids: list[str],
                    feat_cols: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    if backend == "catboost":
        rows = []
        for nid in candidate_nids:
            rows.append(feature_vector(feats, feat_cols) + [nid])
        import numpy as np
        X = np.array(rows, dtype=object)
        for i in range(X.shape[0]):
            for j in range(len(feat_cols)):
                X[i, j] = float(X[i, j])
        proba = model.predict_proba(X)[:, 1]
        for nid, p in zip(candidate_nids, proba):
            scores[nid] = float(p)
    else:
        m = model["model"]
        le = model["le"]
        import numpy as np
        for nid in candidate_nids:
            try:
                enc = le.transform([nid])[0]
            except ValueError:
                scores[nid] = 0.0
                continue
            row = np.array([feature_vector(feats, feat_cols) + [enc]], dtype=float)
            scores[nid] = float(m.predict_proba(row)[0, 1])
    return scores


def prune_to_budget_ml(
    graph,
    top_nodes,
    ml_scores: dict[str, float],
    budget: int,
) -> list:
    from scripts.spikeC.tree_build import _single_dijkstra_all
    start = graph.class_start
    dist = _single_dijkstra_all(graph, start)
    scored = []
    for t in top_nodes:
        d = dist.get(t.nid, 999)
        if d >= 999:
            continue
        score = ml_scores.get(t.nid, 0.0)
        # Ratio of score to path cost (point distance)
        scored.append((score / max(1, d), t.dn, t, d))
    scored.sort(reverse=True, key=lambda x: (x[0], x[1]))

    kept = []
    running = 1  # class start point
    for ratio, _dn, t, d in scored:
        if running + d <= budget:
            kept.append(t)
            running += d
    return kept


def measure_marginals_ml(
    xml: str,
    graph,
    pool: WorkerPool,
    alloc: set[str],
    mastery: dict[int, int],
    ml_scores: dict[str, float],
    *,
    prefer: str = "CombinedDPS",
    max_candidates: int = 40,
) -> tuple[float, dict[str, float]]:
    from scripts.spikeC.tree_marginals import _alloc_for_notable, _dps_from, DPS_KEYS

    mastery_keep = {n: e for n, e in mastery.items() if str(n) in alloc}
    base_xml = render_tree_nodes(xml, alloc, mastery_keep)
    base_res = pool.map([{"xml": base_xml, "stats": list(DPS_KEYS)}])[0]
    base_dps = _dps_from(base_res, prefer, DPS_KEYS)

    candidates = [n for n in main_tree_notables(graph)
                  if n.nid not in alloc and (n.type == "Notable" or (n.type == "Keystone" and n.nid in graph.allocated))]
    if len(candidates) > max_candidates:
        candidates = sorted(candidates, key=lambda n: -ml_scores.get(n.nid, 0.0))[:max_candidates]

    trials: list[tuple[str, str]] = []
    for nd in candidates:
        trial_alloc = _alloc_for_notable(graph, alloc, nd.nid)
        if nd.nid not in trial_alloc or trial_alloc == alloc:
            continue
        m = dict(mastery_keep)
        for nid in trial_alloc:
            if int(nid) in mastery:
                m[int(nid)] = mastery[int(nid)]
        trials.append((nd.nid, render_tree_nodes(xml, trial_alloc, m)))

    if not trials:
        return base_dps, {}

    results = pool.map([{"xml": x, "stats": list(DPS_KEYS)} for _, x in trials])
    marg = {}
    for (nid, _), res in zip(trials, results):
        marg[nid] = _dps_from(res, prefer, DPS_KEYS) - base_dps
    return base_dps, marg


def predict_tree_alloc(
    xml: str,
    model,
    meta: dict,
    backend: str,
    pool: WorkerPool,
    *,
    top_k: int | None = None,
    lambda_blend: float = 0.5,
    prefer: str = "CombinedDPS",
) -> set[str]:
    from scripts.spikeC.tree_marginals import (
        _points_used,
        _notable_targets,
        _alloc_from_notables,
        _alloc_for_notable,
        _parse_mastery,
        _eval_alloc_dps,
        _dps_from,
        DPS_KEYS,
    )
    pob = PobHeadless()
    pob.load_build_xml(xml)
    graph = load_tree_graph(pob)
    _, ascend = split_main_ascend(graph)
    all_notables = main_tree_notables(graph)
    candidates = [n.nid for n in all_notables]

    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    gem_vocab = manifest["gem_vocab"]
    feat_cols = meta["feature_columns"]
    feats = build_features_from_xml(xml, graph, gem_vocab=gem_vocab)

    scores = _score_notables(model, meta, backend, feats, candidates, feat_cols)

    # 1. Start from the ML seed targets
    filtered_notables = [n for n in all_notables if n.type == "Notable" or (n.type == "Keystone" and n.nid in graph.allocated)]
    pruned_nodes = prune_to_budget_ml(graph, filtered_notables, scores, graph.points_total)
    seed_targets = {n.nid for n in pruned_nodes}
    alloc = _alloc_from_notables(graph, seed_targets, ascend)

    budget = graph.points_total
    mastery = _parse_mastery(xml)

    # 2. Greedy fill using blended score (lambda_blend * ML + (1 - lambda_blend) * PoB)
    max_greedy_rounds = 30
    max_candidates = 35
    for rnd in range(max_greedy_rounds):
        if _points_used(graph, alloc) >= budget:
            break
        base_dps, marg = measure_marginals_ml(
            xml, graph, pool, alloc, mastery, scores, prefer=prefer, max_candidates=max_candidates)
        if not marg:
            break

        blended = {}
        for nid, dps_gain in marg.items():
            ml_score = scores.get(nid, 0.0)
            rel_gain = dps_gain / max(1.0, base_dps)
            blended[nid] = lambda_blend * ml_score + (1.0 - lambda_blend) * rel_gain

        added = False
        for best_nid, _ in sorted(blended.items(), key=lambda x: -x[1]):
            new_alloc = _alloc_for_notable(graph, alloc, best_nid)
            if new_alloc == alloc or _points_used(graph, new_alloc) > budget:
                continue
            alloc = new_alloc
            added = True
            break
        if not added:
            break

    # 3. Swap / Backtrack refine (pure DPS-driven to maximize gate metrics)
    max_swap_rounds = 12
    max_swap_trials = 48
    min_notables = 3
    base_dps = _eval_alloc_dps(xml, alloc, mastery, pool, prefer=prefer)

    for rnd in range(max_swap_rounds):
        cur_targets = _notable_targets(graph, alloc)
        if len(cur_targets) < min_notables:
            break

        _, marg = measure_marginals_ml(
            xml, graph, pool, alloc, mastery, scores, prefer=prefer, max_candidates=max_candidates)
        add_cands = sorted(marg.items(), key=lambda x: -x[1])[:max(8, max_candidates // 4)]

        swap_trials = []
        for old_nid in cur_targets:
            for new_nid, gain in add_cands:
                if new_nid in cur_targets:
                    continue
                new_targets = (cur_targets - {old_nid}) | {new_nid}
                new_alloc = _alloc_from_notables(graph, new_targets, ascend)
                if _points_used(graph, new_alloc) > budget:
                    continue
                swap_trials.append((gain, old_nid, new_nid, new_alloc))

        swap_trials.sort(key=lambda x: -x[0])
        swap_trials = swap_trials[:max_swap_trials]

        best_dps = base_dps
        best_alloc = alloc
        best_rec = None

        if swap_trials:
            xmls = []
            meta_list = []
            for _gain, old_nid, new_nid, new_alloc in swap_trials:
                m = {n: e for n, e in mastery.items() if str(n) in new_alloc}
                xmls.append(render_tree_nodes(xml, new_alloc, m))
                meta_list.append((old_nid, new_nid, new_alloc))
            results = pool.map([{"xml": x, "stats": list(DPS_KEYS)} for x in xmls])
            for (old_nid, new_nid, new_alloc), res in zip(meta_list, results):
                dps = _dps_from(res, prefer, DPS_KEYS)
                if dps > best_dps:
                    best_dps = dps
                    best_alloc = new_alloc
                    best_rec = (old_nid, new_nid)

        if best_rec is None:
            # backtrack check
            bt_trials = []
            for old_nid in cur_targets:
                new_targets = cur_targets - {old_nid}
                if len(new_targets) < min_notables:
                    continue
                new_alloc = _alloc_from_notables(graph, new_targets, ascend)
                if _points_used(graph, new_alloc) > budget:
                    continue
                bt_trials.append((old_nid, new_alloc))
            if bt_trials:
                xmls = []
                for _old, new_alloc in bt_trials:
                    m = {n: e for n, e in mastery.items() if str(n) in new_alloc}
                    xmls.append(render_tree_nodes(xml, new_alloc, m))
                results = pool.map([{"xml": x, "stats": list(DPS_KEYS)} for x in xmls])
                for (old_nid, new_alloc), res in zip(bt_trials, results):
                    dps = _dps_from(res, prefer, DPS_KEYS)
                    if dps > best_dps:
                        best_dps = dps
                        best_alloc = new_alloc
                        best_rec = (old_nid, None)

        if best_rec is None or best_alloc == alloc:
            break
        alloc = best_alloc
        base_dps = best_dps

    return alloc


def _eval_one(path: str, pool: WorkerPool, model, meta, backend, lambda_blend: float = 0.5) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = _REPO / p
    xml = load_xml(p)
    pob = PobHeadless()
    pob.load_build_xml(xml)
    graph = load_tree_graph(pob)

    ref = pool.map([{"xml": xml, "stats": list(DPS_KEYS)}])[0]
    prefer = pick_dps_key(ref.get("stats", {}))
    ref_dps = _dps_from(ref, prefer, DPS_KEYS)

    # ML tree
    ml_alloc = predict_tree_alloc(xml, model, meta, backend, pool, lambda_blend=lambda_blend, prefer=prefer)
    spec = ET.fromstring(xml).find("Tree").findall("Spec")[0]
    mastery = parse_mastery_effects(spec)
    ml_xml = render_tree_nodes(xml, ml_alloc, {n: e for n, e in mastery.items() if str(n) in ml_alloc})
    ml_res = pool.map([{"xml": ml_xml, "stats": list(DPS_KEYS)}])[0]
    ml_dps = _dps_from(ml_res, prefer, DPS_KEYS)

    # Hillclimb baseline
    hc_alloc, _ = hillclimb_tree_build(xml, graph, pool, prefer=prefer, max_candidates=35)
    hc_xml = render_tree_nodes(xml, hc_alloc, {n: e for n, e in mastery.items() if str(n) in hc_alloc})
    hc_res = pool.map([{"xml": hc_xml, "stats": list(DPS_KEYS)}])[0]
    hc_dps = _dps_from(hc_res, prefer, DPS_KEYS)

    ml_cmp = compare(ml_alloc, graph)
    hc_cmp = compare(hc_alloc, graph)

    # Enforce budget safety
    assert ml_cmp["our_points"] <= graph.points_total, f"ML budget overflow: {ml_cmp['our_points']} > {graph.points_total}"
    assert hc_cmp["our_points"] <= graph.points_total, f"HC budget overflow: {hc_cmp['our_points']} > {graph.points_total}"

    return {
        "build": str(p.relative_to(_REPO)) if p.is_relative_to(_REPO) else str(p),
        "ml_overlap": ml_cmp["overlap_pct_of_etalon"],
        "hc_overlap": hc_cmp["overlap_pct_of_etalon"],
        "ml_dps_pct": round(ml_dps / ref_dps * 100, 1) if ref_dps else 0,
        "hc_dps_pct": round(hc_dps / ref_dps * 100, 1) if ref_dps else 0,
        "ref_dps": ref_dps,
        "ml_points": ml_cmp["our_points"],
        "hc_points": hc_cmp["our_points"],
        "budget_points": graph.points_total,
    }


def compile_report(rows: list[dict], dps_delta: float = 15.0, ovl_delta: float = 10.0) -> dict:
    import math

    # 1. Clean subset (excluding ref_dps <= 0.1M or ref_dps == 0)
    clean_rows = [r for r in rows if r.get("ref_dps", 0.0) > 100000.0]

    # 2. Raw averages
    ml_dps_avg = sum(r["ml_dps_pct"] for r in rows) / max(1, len(rows))
    hc_dps_avg = sum(r["hc_dps_pct"] for r in rows) / max(1, len(rows))
    ml_ovl_avg = sum(r["ml_overlap"] for r in rows) / max(1, len(rows))
    hc_ovl_avg = sum(r["hc_overlap"] for r in rows) / max(1, len(rows))

    # 3. Clean averages
    n_clean = len(clean_rows)
    if n_clean > 0:
        ml_dps_avg_clean = sum(r["ml_dps_pct"] for r in clean_rows) / n_clean
        hc_dps_avg_clean = sum(r["hc_dps_pct"] for r in clean_rows) / n_clean
        ml_ovl_avg_clean = sum(r["ml_overlap"] for r in clean_rows) / n_clean
        hc_ovl_avg_clean = sum(r["hc_overlap"] for r in clean_rows) / n_clean
    else:
        ml_dps_avg_clean, hc_dps_avg_clean = ml_dps_avg, hc_dps_avg
        ml_ovl_avg_clean, hc_ovl_avg_clean = ml_ovl_avg, hc_ovl_avg

    # 4. Old-style gate verdicts on raw & clean
    dps_pass_raw = ml_dps_avg >= hc_dps_avg + dps_delta
    ovl_pass_raw = ml_ovl_avg >= hc_ovl_avg + ovl_delta
    floor_pass_raw = all(r["ml_dps_pct"] >= r["hc_dps_pct"] - 5.0 for r in rows)
    verdict_raw = "PASS" if (dps_pass_raw or ovl_pass_raw) and floor_pass_raw else "FAIL"

    dps_pass_clean = ml_dps_avg_clean >= hc_dps_avg_clean + dps_delta
    ovl_pass_clean = ml_ovl_avg_clean >= hc_ovl_avg_clean + ovl_delta
    floor_pass_clean = all(r["ml_dps_pct"] >= r["hc_dps_pct"] - 5.0 for r in clean_rows) if n_clean > 0 else floor_pass_raw

    # 5. Robustness metrics (on clean if clean exists, else raw)
    target_rows = clean_rows if n_clean > 0 else rows
    dps_deltas = [r["ml_dps_pct"] - r["hc_dps_pct"] for r in target_rows]
    dps_deltas_sorted = sorted(dps_deltas)
    n_target = len(target_rows)
    if n_target == 0:
        median_dps_delta = 0.0
    elif n_target % 2 == 1:
        median_dps_delta = dps_deltas_sorted[n_target // 2]
    else:
        median_dps_delta = (dps_deltas_sorted[n_target // 2 - 1] + dps_deltas_sorted[n_target // 2]) / 2.0

    wins = sum(1 for d in dps_deltas if d > 0.05)
    losses = sum(1 for d in dps_deltas if d < -0.05)
    ties = sum(1 for d in dps_deltas if abs(d) <= 0.05)

    outliers = [d for d in dps_deltas if abs(d) > 20.0]
    non_outliers = [d for d in dps_deltas if abs(d) <= 20.0]
    avg_delta_excl_outliers = sum(non_outliers) / len(non_outliers) if non_outliers else 0.0

    # 6. Binomial p-value under p=0.5
    def binomial_pval(n, w):
        if n == 0:
            return 1.0
        pval = 0.0
        for k in range(w, n + 1):
            pval += math.comb(n, k) * (0.5 ** n)
        return pval

    p_val = binomial_pval(n_target, wins)
    binomial_pass = p_val <= 0.05

    # Pure Python Bootstrap CI 95%
    import random
    random.seed(42)
    if n_target > 1:
        bootstrap_means = []
        for _ in range(10000):
            resample = [random.choice(dps_deltas) for _ in range(n_target)]
            bootstrap_means.append(sum(resample) / n_target)
        bootstrap_means.sort()
        ci_lower = round(bootstrap_means[250], 2)
        ci_upper = round(bootstrap_means[9750], 2)
    else:
        ci_lower, ci_upper = 0.0, 0.0

    # 7. Consensus Verdict (multi-criteria)
    consensus_pass = ovl_pass_clean and floor_pass_clean and binomial_pass
    consensus_verdict = "PASS" if consensus_pass else "FAIL"

    return {
        "rows": rows,
        "avg": {
            "ml_dps_pct": round(ml_dps_avg, 1),
            "hc_dps_pct": round(hc_dps_avg, 1),
            "ml_overlap": round(ml_ovl_avg, 1),
            "hc_overlap": round(hc_ovl_avg, 1),
        },
        "avg_clean": {
            "ml_dps_pct": round(ml_dps_avg_clean, 1),
            "hc_dps_pct": round(hc_dps_avg_clean, 1),
            "ml_overlap": round(ml_ovl_avg_clean, 1),
            "hc_overlap": round(hc_ovl_avg_clean, 1),
        } if n_clean > 0 else None,
        "robustness": {
            "median_dps_delta": round(median_dps_delta, 1),
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "avg_delta_excl_outliers": round(avg_delta_excl_outliers, 1),
            "binomial_pval": round(p_val, 4),
            "bootstrap_dps_ci_95": [ci_lower, ci_upper],
        },
        "gate": {
            "dps_primary": dps_pass_raw,
            "overlap_secondary": ovl_pass_raw,
            "per_build_dps_floor": floor_pass_raw,
        },
        "gate_clean": {
            "dps_primary": dps_pass_clean,
            "overlap_secondary": ovl_pass_clean,
            "per_build_dps_floor": floor_pass_clean,
            "binomial_significance": binomial_pass,
        } if n_clean > 0 else None,
        "verdict": verdict_raw,
        "consensus_verdict": consensus_verdict,
    }


def eval_holdout(
    builds: list[str] | None = None,
    workers: int | None = 4,
    lambda_blend: float = 0.5,
    use_ninja: bool = False,
    out_path: str | None = None,
) -> dict:
    model_pack, meta, backend = _load_model()
    model = model_pack if backend == "catboost" else model_pack

    if builds:
        paths = builds
    elif use_ninja:
        manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
        paths = [str(_REPO / "corpus" / f"{bid}.pob.xml") for bid in manifest["ninja_holdout_ids"]]
    else:
        paths = GOLD_EVAL

    rows = []
    with WorkerPool(workers) as pool:
        for p in paths:
            rows.append(_eval_one(p, pool, model, meta, backend, lambda_blend=lambda_blend))

    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    gate = manifest.get("gate", {})
    dps_delta = gate.get("dps_delta_pp", 15)
    ovl_delta = gate.get("overlap_delta_pp", 10)

    report = compile_report(rows, dps_delta=dps_delta, ovl_delta=ovl_delta)
    if use_ninja:
        report["holdout"] = "ninja"
        report["n_builds"] = len(rows)

    final_out_path = Path(out_path) if out_path else (OUT_DIR / "eval_report.json")
    final_out_path.parent.mkdir(parents=True, exist_ok=True)
    final_out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    r = eval_holdout()
    print(json.dumps(r, indent=2, ensure_ascii=False))
