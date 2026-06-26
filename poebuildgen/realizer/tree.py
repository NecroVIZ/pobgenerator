from __future__ import annotations

import copy
import heapq
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import lupa
from poebuildgen.headless import PobHeadless, _lua_to_py
from poebuildgen.pool import WorkerPool, _dps_from

DPS_KEYS = ("CombinedDPS", "TotalDPS", "FullDPS")
_MASTERY_RE = re.compile(r"\{(\d+),(\d+)\}")

# Vocabularies for feature extraction
CLASS_VOCAB = ["Marauder", "Ranger", "Witch", "Duelist", "Templar", "Shadow", "Scion"]
ASCEND_VOCAB = [
    "Juggernaut", "Berserker", "Chieftain", "Slayer", "Gladiator", "Champion",
    "Raider", "Deadeye", "Pathfinder", "Assassin", "Saboteur", "Trickster",
    "Necromancer", "Elementalist", "Occultist", "Inquisitor", "Hierophant",
    "Guardian", "Ascendant",
]
DEFAULT_GEM_VOCAB = [
    "Elemental Hit", "Penance Brand", "Flicker Strike", "Arc", "Spark",
    "Righteous Fire", "Summon Raging Spirit", "Animate Guardian",
    "Grace", "Determination", "Hatred", "Wrath", "Anger", "Precision",
    "Vitality", "Clarity", "Defiance Banner", "Malevolence", "Haste",
    "Empower Support", "Enlighten Support", "Enhance Support",
    "Increased Critical Strikes Support", "Multistrike Support",
    "Trinity Support", "Awakened", "Lifetap Support",
]

_GRAPH_LUA = r"""
function()
  local out = {}
  local spec = build.spec
  if not spec or not spec.tree then out.err = "no spec.tree"; return out end
  local nodes = spec.tree.nodes

  local cls = spec.tree.classes[spec.curClassId]
  out.class_start = cls and cls.startNodeId or nil
  out.cur_class = spec.curClassName
  out.cur_ascend = spec.curAscendClassName
  out.class_id = spec.curClassId

  out.nodes = {}
  for id, nd in pairs(nodes) do
    if type(nd) == "table" then
      local linked = {}
      if nd.linkedId then for _, x in ipairs(nd.linkedId) do linked[#linked+1] = x end end
      local sd = {}
      if nd.sd then for _, s in ipairs(nd.sd) do sd[#sd+1] = s end end
      out.nodes[tostring(id)] = {
        type = nd.type or "?",
        dn = nd.dn or "",
        linked = linked,
        sd = sd,
        ascendancy = nd.ascendancyName or "",
      }
    end
  end

  out.allocated = {}
  if spec.allocNodes then
    for id, _ in pairs(spec.allocNodes) do out.allocated[tostring(id)] = true end
  end

  if spec.tree.points then
    out.points_total = spec.tree.points.totalPoints or 0
    out.points_ascend = spec.tree.points.ascendancyPoints or 0
  end

  return out
end
"""

@dataclass
class Node:
    nid: str
    type: str
    dn: str
    linked: list[str]
    sd: list[str]
    ascendancy: str = ""

    @property
    def point_cost(self) -> int:
        return 0 if self.type in ("ClassStart", "AscendClassStart") else 1


@dataclass
class TreeGraph:
    nodes: dict[str, Node]
    class_start: str
    cur_class: str
    cur_ascend: str
    class_id: int
    allocated: set[str]
    points_total: int
    points_ascend: int

    def notables(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.type == "Notable"]

    def keystones(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.type == "Keystone"]


def load_tree_graph(pob: PobHeadless) -> TreeGraph:
    fn = pob.eval(_GRAPH_LUA)
    raw = _lua_to_py(fn())
    if isinstance(raw, dict) and raw.get("err"):
        raise RuntimeError(f"PoB tree-graph export failed: {raw['err']}")
    nodes: dict[str, Node] = {}
    for nid, info in (raw.get("nodes") or {}).items():
        if not isinstance(info, dict):
            continue
        nodes[nid] = Node(
            nid=nid, type=info.get("type", "?"), dn=info.get("dn", ""),
            linked=[str(x) for x in (info.get("linked") or [])],
            sd=list(info.get("sd") or []),
            ascendancy=str(info.get("ascendancy") or ""),
        )
    return TreeGraph(
        nodes=nodes,
        class_start=str(raw.get("class_start")),
        cur_class=raw.get("cur_class", "?"),
        cur_ascend=raw.get("cur_ascend", "?"),
        class_id=int(raw.get("class_id") or 0),
        allocated={str(x) for x in ((raw.get("allocated") or {}).keys())},
        points_total=int(raw.get("points_total") or 0),
        points_ascend=int(raw.get("points_ascend") or 0),
    )


# --- XML Utilities ---

def parse_mastery_effects(spec: ET.Element) -> dict[int, int]:
    raw = spec.get("masteryEffects") or ""
    return {int(n): int(e) for n, e in _MASTERY_RE.findall(raw)}


def render_tree_nodes(xml: str, node_ids: set[str | int],
                      mastery_effects: dict[int, int] | None = None) -> str:
    root = copy.deepcopy(ET.fromstring(xml))
    tree = root.find("Tree")
    if tree is None:
        return xml
    specs = tree.findall("Spec")
    if not specs:
        return xml
    active = int(tree.get("activeSpec") or 1)
    spec = specs[min(active - 1, len(specs) - 1)]
    ids = sorted(int(n) for n in node_ids)
    spec.set("nodes", ",".join(str(i) for i in ids))
    if mastery_effects is not None:
        spec.set("masteryEffects", ",".join(f"{{{int(n)},{int(e)}}}" for n, e in mastery_effects.items()))
    return ET.tostring(root, encoding="unicode")


def mastery_subset(etalon: dict[int, int], alloc: set[str]) -> dict[int, int]:
    return {n: e for n, e in etalon.items() if str(n) in alloc}


# --- Dijkstra Algorithms ---

def _single_dijkstra_all(graph: TreeGraph, start: str) -> dict[str, int]:
    dist: dict[str, int] = {start: 0}
    pq = [(0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float("inf")):
            continue
        nd = graph.nodes.get(u)
        if not nd:
            continue
        for v in nd.linked:
            v = str(v)
            vn = graph.nodes.get(v)
            if not vn:
                continue
            nd2 = d + vn.point_cost
            if nd2 < dist.get(v, float("inf")):
                dist[v] = nd2
                heapq.heappush(pq, (nd2, v))
    return dist


def dijkstra_path(graph: TreeGraph, start: str, target: str) -> set[str]:
    if start not in graph.nodes or target not in graph.nodes:
        return set()
    dist: dict[str, int] = {start: 0}
    parent: dict[str, str] = {}
    pq = [(0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == target:
            break
        if d > dist.get(u, float("inf")):
            continue
        nd = graph.nodes.get(u)
        if not nd:
            continue
        for v in nd.linked:
            v = str(v)
            vn = graph.nodes.get(v)
            if not vn or vn.ascendancy:  # main tree only
                continue
            nd2 = d + vn.point_cost
            if nd2 < dist.get(v, float("inf")):
                dist[v] = nd2
                parent[v] = u
                heapq.heappush(pq, (nd2, v))
    if target not in dist:
        return set()
    path: set[str] = {target}
    cur = target
    while cur != start:
        cur = parent.get(cur, start)
        path.add(cur)
        if cur == start:
            break
    return path


def dijkstra_to_targets(graph: TreeGraph, targets: set[str]) -> tuple[set[str], int]:
    if graph.class_start not in graph.nodes:
        raise RuntimeError(f"class_start {graph.class_start} не в графе")
    start = graph.class_start
    dist: dict[str, int] = {start: 0}
    pq = [(0, start)]
    remaining = set(targets)
    remaining.discard(start)
    while pq and remaining:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float("inf")):
            continue
        if u in remaining:
            remaining.discard(u)
        nd = graph.nodes.get(u)
        if not nd:
            continue
        for v in nd.linked:
            v = str(v)
            vn = graph.nodes.get(v)
            if not vn:
                continue
            cost = vn.point_cost
            nd2 = d + cost
            if nd2 < dist.get(v, float("inf")):
                dist[v] = nd2
                heapq.heappush(pq, (nd2, v))

    allocated: set[str] = {start}
    for t in targets:
        if t == start:
            continue
        if t not in dist:
            continue
        cur = t
        guard = 0
        while cur != start and guard < 5000:
            allocated.add(cur)
            cur_nd = graph.nodes.get(cur)
            if not cur_nd:
                break
            best, best_d = None, dist.get(cur, 0)
            for v in cur_nd.linked:
                v = str(v)
                dv = dist.get(v)
                if dv is None:
                    continue
                if dv < best_d:
                    best_d, best = dv, v
            if best is None:
                break
            cur = best
            guard += 1
        else:
            allocated.add(cur)

    total_points = sum(graph.nodes[n].point_cost for n in allocated if n in graph.nodes)
    return allocated, total_points


def split_main_ascend(graph: TreeGraph) -> tuple[set[str], set[str]]:
    main, ascend = set(), set()
    for nid in graph.allocated:
        nd = graph.nodes.get(nid)
        if not nd:
            continue
        if nd.ascendancy or nd.type in ("AscendClassStart",):
            ascend.add(nid)
        else:
            main.add(nid)
    return main, ascend


def main_tree_notables(graph: TreeGraph) -> list[Node]:
    return [n for n in graph.notables() + graph.keystones() if not n.ascendancy]


# --- ML model helper and features ---

def load_ml_model(model_path: str | None = None, config_path: str | None = None) -> tuple[Any, dict, str]:
    project_root = Path(__file__).resolve().parents[2]
    out_dir = project_root / "corpus" / "ml_v0"
    
    cpath = Path(config_path) if config_path else out_dir / "train_meta.json"
    meta = json.loads(cpath.read_text(encoding="utf-8"))
    
    mpath = Path(model_path) if model_path else out_dir / meta["model_path"]
    
    if meta["backend"] == "catboost":
        from catboost import CatBoostClassifier
        model = CatBoostClassifier()
        model.load_model(str(mpath))
        return model, meta, "catboost"
        
    import joblib
    pack = joblib.load(mpath)
    return pack, meta, "sklearn"


def build_features_from_xml(xml: str, graph: TreeGraph, gem_vocab: list[str]) -> dict[str, Any]:
    keystones = []
    for nid in graph.allocated:
        if nid in graph.nodes and graph.nodes[nid].type == "Keystone":
            keystones.append(graph.nodes[nid].dn)
            
    # parse gem names
    root = ET.fromstring(xml)
    gems = []
    for gem in root.iter("Gem"):
        name = gem.get("nameSpec") or gem.get("skillId") or ""
        if name:
            gems.append(name)
            
    meta = {
        "class": graph.cur_class,
        "ascendancy": graph.cur_ascend,
        "allGemNames": gems,
        "level": 100,
        "passiveCount": len(graph.allocated),
        "keyStones": keystones,
    }
    
    # build features
    feats: dict[str, Any] = {
        "level": 1.0,
        "passive_count": float(meta["passiveCount"]) / 130.0,
        "main_skill": gems[0] if gems else "",
    }
    
    # skip support
    for g in gems:
        if "Support" not in g and g not in ("Steelskin", "Frostblink", "Whirling Blades", "Cast when Damage Taken Support"):
            feats["main_skill"] = g
            break
            
    def one_hot(val: str, vocab: list[str], prefix: str):
        return {f"{prefix}_{x}": int(x == val) for x in vocab}
        
    feats.update(one_hot(meta["class"], CLASS_VOCAB, "class"))
    feats.update(one_hot(meta["ascendancy"], ASCEND_VOCAB, "asc"))
    
    gset = set(gems)
    for v in gem_vocab:
        feats[f"gem_{v}"] = int(v in gset)
        
    ks = set(keystones)
    for name in ("Iron Reflexes", "Mind Over Matter", "Ghost Reaver", "Blood Magic",
                 "Chaos Inoculation", "Resolute Technique", "Avatar of Fire",
                 "Eldritch Battery", "Ancestral Bond", "Pain Attunement"):
        feats[f"ks_{name}"] = int(name in ks)
        
    main_skill = feats["main_skill"]
    has_crit_gem = any("crit" in g.lower() for g in gems)
    feats["is_crit"] = int(has_crit_gem and "Resolute Technique" not in ks)
    
    dot_keywords = {"dot", "decay", "ignite", "poison", "bleed", "affliction", "ailment", "cruelty", "void manipulation", "over time"}
    has_dot_gem = any(any(kw in g.lower() for kw in dot_keywords) for g in gems)
    dot_skills = {"righteous fire", "blight", "essence drain", "contagion", "toxic rain", "caustic arrow", "soulrend", "scourge arrow", "poisonous concoction"}
    main_skill_lower = main_skill.lower()
    feats["is_dot"] = int(has_dot_gem or any(ds in main_skill_lower for ds in dot_skills))
    
    minion_keywords = {"minion", "summon", "raise", "guardian", "golem", "spectre", "zombie", "animate", "phastasm", "holy relic", "absolution"}
    has_minion_gem = any(any(kw in g.lower() for kw in minion_keywords) for g in gems)
    feats["is_minion"] = int(has_minion_gem or meta["ascendancy"] == "Necromancer")
    
    conv_keywords = {"conversion", "convert", "cold to fire", "physical to lightning", "trinity"}
    has_conv_gem = any(any(kw in g.lower() for kw in conv_keywords) for g in gems)
    feats["is_conversion"] = int(has_conv_gem or "Avatar of Fire" in ks)
    
    es_keystones = {"Chaos Inoculation", "Ghost Reaver", "Eldritch Battery", "Pain Attunement"}
    has_es_ks = any(k in ks for k in es_keystones)
    feats["is_es_based"] = int(has_es_ks or meta["ascendancy"] in ("Occultist", "Trickster"))
    
    feats["is_life_based"] = int("Chaos Inoculation" not in ks)
    
    return feats


def _score_notables(model, meta: dict, backend: str, feats: dict, candidate_nids: list[str],
                    feat_cols: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    if backend == "catboost":
        rows = []
        for nid in candidate_nids:
            row = [float(feats.get(c, 0)) for c in feat_cols] + [nid]
            rows.append(row)
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
            row = np.array([[float(feats.get(c, 0)) for c in feat_cols] + [enc]], dtype=float)
            scores[nid] = float(m.predict_proba(row)[0, 1])
    return scores


def prune_to_budget_ml(
    graph: TreeGraph,
    top_nodes: list[Node],
    ml_scores: dict[str, float],
    budget: int,
) -> list[Node]:
    start = graph.class_start
    dist = _single_dijkstra_all(graph, start)
    scored = []
    for t in top_nodes:
        d = dist.get(t.nid, 999)
        if d >= 999:
            continue
        score = ml_scores.get(t.nid, 0.0)
        scored.append((score / max(1, d), t.dn, t, d))
    scored.sort(reverse=True, key=lambda x: (x[0], x[1]))

    kept = []
    running = 1
    for ratio, _dn, t, d in scored:
        if running + d <= budget:
            kept.append(t)
            running += d
    return kept


# --- Heuristic Scoring ---

_HEUR_PATTERNS = {
    "life": [re.compile(r"maximum Life", re.I), re.compile(r"to Strength", re.I)],
    "es": [re.compile(r"maximum Energy Shield", re.I), re.compile(r"to Intelligence", re.I)],
    "eva": [re.compile(r"Evasion", re.I)],
    "armour": [re.compile(r"Armour", re.I)],
    "suppress": [re.compile(r"Suppress", re.I)],
    "resist": [re.compile(r"Resistance", re.I)],
    "dmg": [re.compile(r"Damage", re.I)],
}

def score_node_heuristic(node: Node, wanted: set[str]) -> float:
    text = " | ".join(node.sd)
    score = 0.0
    for tag, patterns in _HEUR_PATTERNS.items():
        if tag in wanted:
            if any(p.search(text) for p in patterns):
                score += 1.0
    return score


def derive_wanted_heuristic(graph: TreeGraph) -> set[str]:
    # Heuristics based on reference tree
    wanted = {"life", "dmg"}
    for nid in graph.allocated:
        nd = graph.nodes.get(nid)
        if not nd:
            continue
        text = " | ".join(nd.sd).lower()
        if "energy shield" in text:
            wanted.add("es")
        if "evasion" in text:
            wanted.add("eva")
        if "armour" in text:
            wanted.add("armour")
        if "suppress" in text:
            wanted.add("suppress")
        if "resistance" in text:
            wanted.add("resist")
    return wanted


def prune_to_budget_heuristic(
    graph: TreeGraph,
    top_nodes: list[Node],
    wanted: set[str],
    budget: int,
) -> list[Node]:
    start = graph.class_start
    dist = _single_dijkstra_all(graph, start)
    scored = []
    for t in top_nodes:
        d = dist.get(t.nid, 999)
        if d >= 999:
            continue
        score = score_node_heuristic(t, wanted)
        scored.append((score / max(1, d), t.dn, t, d))
    scored.sort(reverse=True, key=lambda x: (x[0], x[1]))

    kept = []
    running = 1
    for ratio, _dn, t, d in scored:
        if running + d <= budget:
            kept.append(t)
            running += d
    return kept


# --- Tree Build phases ---

def _alloc_for_notable(graph: TreeGraph, base_alloc: set[str], notable_id: str) -> set[str]:
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
    main, _ = dijkstra_to_targets(graph, set(targets) | {graph.class_start})
    return main | set(ascend_base)


def _eval_alloc_dps(
    xml: str,
    alloc: set[str],
    mastery: dict[int, int],
    pool: WorkerPool,
    *,
    prefer: str,
    fingerprint: dict | None = None,
) -> float:
    m = {n: e for n, e in mastery.items() if str(n) in alloc}
    gxml = render_tree_nodes(xml, alloc, m)
    req = {"xml": gxml, "stats": list(DPS_KEYS)}
    if fingerprint:
        req["fingerprint"] = fingerprint
    res = pool.map([req])[0]
    return _dps_from(res, prefer, DPS_KEYS)


def measure_marginals(
    xml: str,
    graph: TreeGraph,
    pool: WorkerPool,
    alloc: set[str],
    mastery: dict[int, int],
    scores: dict[str, float],
    *,
    prefer: str = "CombinedDPS",
    max_candidates: int = 40,
    fingerprint: dict | None = None,
) -> tuple[float, dict[str, float]]:
    mastery_keep = {n: e for n, e in mastery.items() if str(n) in alloc}
    base_xml = render_tree_nodes(xml, alloc, mastery_keep)
    base_req = {"xml": base_xml, "stats": list(DPS_KEYS)}
    if fingerprint:
        base_req["fingerprint"] = fingerprint
    base_res = pool.map([base_req])[0]
    base_dps = _dps_from(base_res, prefer, DPS_KEYS)

    candidates = [n for n in main_tree_notables(graph)
                  if n.nid not in alloc and (n.type == "Notable" or (n.type == "Keystone" and n.nid in graph.allocated))]
    if len(candidates) > max_candidates:
        candidates = sorted(candidates, key=lambda n: -scores.get(n.nid, 0.0))[:max_candidates]

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

    reqs = [{"xml": x, "stats": list(DPS_KEYS)} for _, x in trials]
    if fingerprint:
        for r in reqs:
            r["fingerprint"] = fingerprint
    results = pool.map(reqs)
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
    graph: TreeGraph,
    *,
    lambda_blend: float = 0.5,
    prefer: str = "CombinedDPS",
    fingerprint: dict | None = None,
    max_greedy_rounds: int = 30,
    max_swap_rounds: int = 12,
    max_candidates: int = 35,
    max_swap_trials: int = 48,
) -> set[str]:
    _, ascend = split_main_ascend(graph)
    all_notables = main_tree_notables(graph)
    candidates = [n.nid for n in all_notables]

    gem_vocab = meta.get("gem_vocab") or DEFAULT_GEM_VOCAB
    feat_cols = meta["feature_columns"]
    feats = build_features_from_xml(xml, graph, gem_vocab=gem_vocab)

    scores = _score_notables(model, meta, backend, feats, candidates, feat_cols)

    # 1. Start from the ML seed targets
    filtered_notables = [n for n in all_notables if n.type == "Notable" or (n.type == "Keystone" and n.nid in graph.allocated)]
    pruned_nodes = prune_to_budget_ml(graph, filtered_notables, scores, graph.points_total)
    seed_targets = {n.nid for n in pruned_nodes}
    alloc = _alloc_from_notables(graph, seed_targets, ascend)

    budget = graph.points_total
    root = ET.fromstring(xml)
    spec_el = root.find("Tree").findall("Spec")[0]
    mastery = parse_mastery_effects(spec_el)

    # 2. Greedy fill using blended score (lambda_blend * ML + (1 - lambda_blend) * PoB)
    for rnd in range(max_greedy_rounds):
        if _points_used(graph, alloc) >= budget:
            break
        base_dps, marg = measure_marginals(
            xml, graph, pool, alloc, mastery, scores, prefer=prefer, max_candidates=max_candidates, fingerprint=fingerprint)
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

    # 3. Swap / Backtrack refine (pure DPS-driven)
    min_notables = 3
    base_dps = _eval_alloc_dps(xml, alloc, mastery, pool, prefer=prefer, fingerprint=fingerprint)

    for rnd in range(max_swap_rounds):
        cur_targets = _notable_targets(graph, alloc)
        if len(cur_targets) < min_notables:
            break

        _, marg = measure_marginals(
            xml, graph, pool, alloc, mastery, scores, prefer=prefer, max_candidates=max_candidates, fingerprint=fingerprint)
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
            reqs = [{"xml": x, "stats": list(DPS_KEYS)} for x in xmls]
            if fingerprint:
                for r in reqs:
                    r["fingerprint"] = fingerprint
            results = pool.map(reqs)
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
                reqs = [{"xml": x, "stats": list(DPS_KEYS)} for x in xmls]
                if fingerprint:
                    for r in reqs:
                        r["fingerprint"] = fingerprint
                results = pool.map(reqs)
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


def optimize_tree_heuristic(
    xml: str,
    graph: TreeGraph,
    pool: WorkerPool,
    *,
    prefer: str = "CombinedDPS",
    max_greedy_rounds: int = 25,
    max_candidates: int = 35,
    fingerprint: dict | None = None,
    max_swap_rounds: int = 12,
    max_swap_trials: int = 48,
) -> set[str]:
    _, ascend = split_main_ascend(graph)
    all_notables = main_tree_notables(graph)
    
    wanted = derive_wanted_heuristic(graph)
    scores = {n.nid: score_node_heuristic(n, wanted) for n in all_notables}

    # 1. Start from the heuristic seed targets
    pruned_nodes = prune_to_budget_heuristic(graph, all_notables, wanted, graph.points_total)
    seed_targets = {n.nid for n in pruned_nodes}
    alloc = _alloc_from_notables(graph, seed_targets, ascend)

    budget = graph.points_total
    root = ET.fromstring(xml)
    spec_el = root.find("Tree").findall("Spec")[0]
    mastery = parse_mastery_effects(spec_el)

    # 2. Greedy fill using heuristic score
    for rnd in range(max_greedy_rounds):
        if _points_used(graph, alloc) >= budget:
            break
        base_dps, marg = measure_marginals(
            xml, graph, pool, alloc, mastery, scores, prefer=prefer, max_candidates=max_candidates, fingerprint=fingerprint)
        if not marg:
            break

        blended = {}
        for nid, dps_gain in marg.items():
            h_score = scores.get(nid, 0.0)
            rel_gain = dps_gain / max(1.0, base_dps)
            blended[nid] = 0.5 * h_score + 0.5 * rel_gain

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

    # 3. Swap / Backtrack refine
    min_notables = 3
    base_dps = _eval_alloc_dps(xml, alloc, mastery, pool, prefer=prefer, fingerprint=fingerprint)

    for rnd in range(max_swap_rounds):
        cur_targets = _notable_targets(graph, alloc)
        if len(cur_targets) < min_notables:
            break

        _, marg = measure_marginals(
            xml, graph, pool, alloc, mastery, scores, prefer=prefer, max_candidates=max_candidates, fingerprint=fingerprint)
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
            reqs = [{"xml": x, "stats": list(DPS_KEYS)} for x in xmls]
            if fingerprint:
                for r in reqs:
                    r["fingerprint"] = fingerprint
            results = pool.map(reqs)
            for (old_nid, new_nid, new_alloc), res in zip(meta_list, results):
                dps = _dps_from(res, prefer, DPS_KEYS)
                if dps > best_dps:
                    best_dps = dps
                    best_alloc = new_alloc
                    best_rec = (old_nid, new_nid)

        if best_rec is None:
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
                reqs = [{"xml": x, "stats": list(DPS_KEYS)} for x in xmls]
                if fingerprint:
                    for r in reqs:
                        r["fingerprint"] = fingerprint
                results = pool.map(reqs)
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
