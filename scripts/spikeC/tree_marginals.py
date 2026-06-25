"""PoB-маржиналы по notables дерева: ΔDPS за кратчайший путь + узел.

Базовая линия: эталонная ascendancy + class_start, шмот — полный референс.
Кандидаты: main-tree notables/keystones (не ascendancy). Батч через WorkerPool.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from poebuildgen import pobcode
from poebuildgen.headless import PobHeadless
from poebuildgen.pool import WorkerPool, _dps_from
from scripts.spikeC.tree_build import (
    TreeGraph,
    dijkstra_path,
    dijkstra_to_targets,
    load_tree_graph,
    main_tree_notables,
    score_node,
    split_main_ascend,
    derive_wanted_from_etalon,
)
from scripts.spikeC.tree_xml import parse_mastery_effects, render_tree_nodes

DPS_KEYS = ("CombinedDPS", "TotalDPS", "FullDPS")


def _alloc_for_notable(graph: TreeGraph, base_alloc: set[str], notable_id: str) -> set[str]:
    """Union base_alloc + shortest main-tree path to notable."""
    path = dijkstra_path(graph, graph.class_start, notable_id)
    if not path:
        return set()
    out = set(base_alloc)
    for nid in path:
        nd = graph.nodes.get(nid)
        if nd and nd.type != "Mastery":
            out.add(nid)
    return out


def _points_used(graph: TreeGraph, alloc: set[str]) -> int:
    return sum(graph.nodes[n].point_cost for n in alloc if n in graph.nodes)


def _notable_targets(graph: TreeGraph, alloc: set[str]) -> set[str]:
    return {n for n in alloc if n in graph.nodes
            and graph.nodes[n].type in ("Notable", "Keystone")
            and not graph.nodes[n].ascendancy}


def _alloc_from_notables(graph: TreeGraph, targets: set[str], ascend_base: set[str]) -> set[str]:
    """Rebuild full alloc: ascend (fixed) + Dijkstra union to target notables."""
    main, _ = dijkstra_to_targets(graph, set(targets) | {graph.class_start})
    return main | set(ascend_base)


def _eval_alloc_dps(
    xml: str,
    alloc: set[str],
    mastery: dict[int, int],
    pool: WorkerPool,
    *,
    prefer: str,
) -> float:
    m = {n: e for n, e in mastery.items() if str(n) in alloc}
    gxml = render_tree_nodes(xml, alloc, m)
    res = pool.map([{"xml": gxml, "stats": list(DPS_KEYS)}])[0]
    return _dps_from(res, prefer, DPS_KEYS)


def _parse_mastery(xml: str) -> dict[int, int]:
    root = ET.fromstring(xml)
    spec = root.find("Tree").findall("Spec")[0]  # type: ignore[union-attr]
    return parse_mastery_effects(spec)


def measure_marginals_from_alloc(
    xml: str,
    graph: TreeGraph,
    pool: WorkerPool,
    alloc: set[str],
    mastery: dict[int, int],
    *,
    prefer: str = "CombinedDPS",
    max_candidates: int = 40,
) -> tuple[float, dict[str, float]]:
    """ΔDPS от текущего alloc при добавлении одного notable (кратчайший путь)."""
    mastery_keep = {n: e for n, e in mastery.items() if str(n) in alloc}
    base_xml = render_tree_nodes(xml, alloc, mastery_keep)
    base_res = pool.map([{"xml": base_xml, "stats": list(DPS_KEYS)}])[0]
    base_dps = _dps_from(base_res, prefer, DPS_KEYS)

    candidates = [n for n in main_tree_notables(graph)
                  if n.nid not in alloc and n.type in ("Notable", "Keystone")]
    if len(candidates) > max_candidates:
        wanted = derive_wanted_from_etalon(graph)
        candidates = sorted(candidates, key=lambda n: score_node(n, wanted), reverse=True)[:max_candidates]

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


def greedy_tree_build(
    xml: str,
    graph: TreeGraph,
    pool: WorkerPool,
    *,
    prefer: str = "CombinedDPS",
    max_rounds: int = 25,
    max_candidates: int = 35,
    min_gain_frac: float = 0.005,
    alloc: set[str] | None = None,
) -> tuple[set[str], list[dict]]:
    """Жадно наращивать дерево по PoB-маржиналам от текущего состояния."""
    if alloc is None:
        _, ascend = split_main_ascend(graph)
        alloc = set(ascend) | {graph.class_start}
    else:
        alloc = set(alloc)
    budget = graph.points_total
    root = ET.fromstring(xml)
    spec = root.find("Tree").findall("Spec")[0]  # type: ignore[union-attr]
    mastery = parse_mastery_effects(spec)
    history: list[dict] = []

    for rnd in range(max_rounds):
        if _points_used(graph, alloc) >= budget:
            break
        base_dps, marg = measure_marginals_from_alloc(
            xml, graph, pool, alloc, mastery, prefer=prefer, max_candidates=max_candidates)
        if not marg:
            break
        added = False
        for best_nid, best_gain in sorted(marg.items(), key=lambda x: -x[1]):
            if best_gain < base_dps * min_gain_frac:
                break
            new_alloc = _alloc_for_notable(graph, alloc, best_nid)
            if new_alloc == alloc or _points_used(graph, new_alloc) > budget:
                continue
            alloc = new_alloc
            nd = graph.nodes.get(best_nid)
            history.append({"round": rnd, "notable": nd.dn if nd else best_nid,
                            "gain": best_gain, "dps": base_dps + best_gain})
            added = True
            break
        if not added:
            break
    return alloc, history


def hillclimb_tree_build(
    xml: str,
    graph: TreeGraph,
    pool: WorkerPool,
    *,
    prefer: str = "CombinedDPS",
    max_greedy_rounds: int = 25,
    max_candidates: int = 35,
    max_swap_rounds: int = 12,
    max_swap_trials: int = 48,
    min_notables: int = 3,
    min_gain_frac: float = 0.005,
    alloc: set[str] | None = None,
) -> tuple[set[str], list[dict]]:
    """Greedy add-only, then PoB hill-climb: swap notable + optional backtrack."""
    alloc, history = greedy_tree_build(
        xml, graph, pool, prefer=prefer, max_rounds=max_greedy_rounds,
        max_candidates=max_candidates, min_gain_frac=min_gain_frac, alloc=alloc)
    _, ascend_base = split_main_ascend(graph)
    mastery = _parse_mastery(xml)
    budget = graph.points_total
    base_dps = _eval_alloc_dps(xml, alloc, mastery, pool, prefer=prefer)

    for rnd in range(max_swap_rounds):
        cur_targets = _notable_targets(graph, alloc)
        if len(cur_targets) < min_notables:
            break
        _, marg = measure_marginals_from_alloc(
            xml, graph, pool, alloc, mastery, prefer=prefer, max_candidates=max_candidates)
        add_cands = sorted(marg.items(), key=lambda x: -x[1])[:max(8, max_candidates // 4)]

        swap_trials: list[tuple[float, str, str, set[str]]] = []
        for old_nid in cur_targets:
            for new_nid, gain in add_cands:
                if new_nid in cur_targets:
                    continue
                new_targets = (cur_targets - {old_nid}) | {new_nid}
                new_alloc = _alloc_from_notables(graph, new_targets, ascend_base)
                if _points_used(graph, new_alloc) > budget:
                    continue
                swap_trials.append((gain, old_nid, new_nid, new_alloc))
        swap_trials.sort(key=lambda x: -x[0])
        swap_trials = swap_trials[:max_swap_trials]

        best_dps = base_dps
        best_alloc = alloc
        best_rec: dict | None = None

        if swap_trials:
            xmls = []
            meta: list[tuple[str, str, set[str]]] = []
            for _gain, old_nid, new_nid, new_alloc in swap_trials:
                m = {n: e for n, e in mastery.items() if str(n) in new_alloc}
                xmls.append(render_tree_nodes(xml, new_alloc, m))
                meta.append((old_nid, new_nid, new_alloc))
            results = pool.map([{"xml": x, "stats": list(DPS_KEYS)} for x in xmls])
            for (old_nid, new_nid, new_alloc), res in zip(meta, results):
                dps = _dps_from(res, prefer, DPS_KEYS)
                if dps > best_dps:
                    best_dps = dps
                    best_alloc = new_alloc
                    od = graph.nodes.get(old_nid)
                    nd = graph.nodes.get(new_nid)
                    best_rec = {"round": f"swap_{rnd}", "out": od.dn if od else old_nid,
                                "in": nd.dn if nd else new_nid, "dps": dps}

        if best_rec is None:
            bt_trials: list[tuple[str, set[str]]] = []
            for old_nid in cur_targets:
                new_targets = cur_targets - {old_nid}
                if len(new_targets) < min_notables:
                    continue
                new_alloc = _alloc_from_notables(graph, new_targets, ascend_base)
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
                        od = graph.nodes.get(old_nid)
                        best_rec = {"round": f"backtrack_{rnd}", "out": od.dn if od else old_nid,
                                    "in": None, "dps": dps}

        if best_rec is None or best_alloc == alloc:
            break
        history.append(best_rec)
        alloc = best_alloc
        base_dps = best_dps

    return alloc, history


def measure_notable_marginals(
    xml: str,
    graph: TreeGraph,
    pool: WorkerPool,
    *,
    prefer: str = "CombinedDPS",
    max_candidates: int = 80,
    prefilter_sd: bool = True,
) -> tuple[float, dict[str, float]]:
    """Вернуть (baseline_dps, {notable_id: marginal_dps})."""
    _, ascend = split_main_ascend(graph)
    baseline_nodes = set(ascend) | {graph.class_start}

    root = ET.fromstring(xml)
    spec = root.find("Tree").findall("Spec")[0]  # type: ignore[union-attr]
    mastery = parse_mastery_effects(spec)
    # оставляем mastery только для узлов в baseline (ascend)
    mastery_keep = {n: e for n, e in mastery.items() if str(n) in baseline_nodes}

    baseline_xml = render_tree_nodes(xml, baseline_nodes, mastery_keep)
    base_res = pool.map([{"xml": baseline_xml, "stats": list(DPS_KEYS)}])[0]
    base_dps = _dps_from(base_res, prefer, DPS_KEYS)

    candidates = main_tree_notables(graph)
    if prefilter_sd and len(candidates) > max_candidates:
        wanted = derive_wanted_from_etalon(graph)
        candidates = sorted(candidates, key=lambda n: score_node(n, wanted), reverse=True)[:max_candidates]

    trials: list[tuple[str, str]] = []  # (nid, xml)
    for nd in candidates:
        if nd.nid in baseline_nodes:
            continue
        alloc = _alloc_for_notable(graph, baseline_nodes, nd.nid)
        if not alloc or nd.nid not in alloc:
            continue
        m = dict(mastery_keep)
        for nid in alloc:
            if nid in mastery:
                m[int(nid)] = mastery[int(nid)]
        trials.append((nd.nid, render_tree_nodes(xml, alloc, m)))

    if not trials:
        return base_dps, {}

    reqs = [{"xml": x, "stats": list(DPS_KEYS)} for _, x in trials]
    results = pool.map(reqs)
    marg: dict[str, float] = {}
    for (nid, _), res in zip(trials, results):
        dps = _dps_from(res, prefer, DPS_KEYS)
        marg[nid] = dps - base_dps
    return base_dps, marg


def load_build_xml(path: str) -> tuple[str, TreeGraph]:
    from pathlib import Path
    p = Path(path)
    xml = pobcode.decode(p.read_text(encoding="utf-8").strip()).decode("utf-8")
    pob = PobHeadless()
    pob.load_build_xml(xml)
    graph = load_tree_graph(pob)
    return xml, graph


def pick_dps_key(stats: dict) -> str:
    best, bv = "CombinedDPS", -1.0
    for k in DPS_KEYS:
        v = stats.get(k) or 0
        if v > bv:
            best, bv = k, v
    return best
