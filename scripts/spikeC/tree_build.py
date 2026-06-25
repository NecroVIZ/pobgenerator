"""Спайк дерева (D14): построить дерево билда через target-set + Dijkstra + budget-prune,
сравнить с эталоном (реальным деревом того же билда).

Архитектура спайка (по итогам зонда scripts/spikeC_tree_probe.py):
  1. Загрузить реальный билд (эталон) в PoB.
  2. Выгрузить граф дерева (nodes + linkedId) и список notables со stat-описаниями (sd) в Python.
  3. Запомнить эталонный target-set = allocated notables/keystones реального билда.
  4. Построить НАШ target-set: score всех notables дерева матчингом sd против "нужд точки осей".
     Точка осей = выводится из эталона (какие статы доминируют: life/ES/suppression/ele-dmg/...).
  5. Dijkstra-stitch: кратчайшие пути (по числу small-узлов) от стартовой точки класса к каждому
     target-notable; union путей = allocated-сет.
  6. Budget-prune: если Σ очков > бюджета — отбросить цели с наименьшим score/point.
  7. Сравнить наше дерево vs эталон: overlap target-sets, total points, stat-покрытие.

Честная цель спайка: НЕ "собрать дерево лучше человека", а "доказать, что Dijkstra-аппроксимация
+ sd-parse target-set даёт дерево с разумным overlap (>=50% эталонных notables) при равном бюджете,
и что остаток закрывается hill-climb (Phase 1)". Если overlap <30% — D14 в текущем виде не работает.
"""
from __future__ import annotations

import heapq
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable

from poebuildgen import pobcode
from poebuildgen.headless import PobHeadless, _lua_to_py

# --- Lua: выгрузить граф дерева + notables в Python ---

_GRAPH_LUA = r"""
function()
  local out = {}
  local spec = build.spec
  if not spec or not spec.tree then out.err = "no spec.tree"; return out end
  local nodes = spec.tree.nodes

  -- стартовая точка класса
  local cls = spec.tree.classes[spec.curClassId]
  out.class_start = cls and cls.startNodeId or nil
  out.cur_class = spec.curClassName
  out.cur_ascend = spec.curAscendClassName
  out.class_id = spec.curClassId

  -- все узлы: id -> {type, linked (соседние id), sd (описания модов), dn}
  -- оптимизация: выгружаем только то, что нужно (type + linked + sd + dn), не весь объект
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

  -- эталонный allocated-set (id -> true)
  out.allocated = {}
  if spec.allocNodes then
    for id, _ in pairs(spec.allocNodes) do out.allocated[tostring(id)] = true end
  end

  -- бюджет очков (totalPoints + ascendancyPoints)
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
    # cost = сколько очков тратит (1 для Normal/Socket/Notable/Keystone, 0 для ClassStart/Ascend)
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
    allocated: set[str]           # эталон (если загружен реальный билд)
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


# --- Target-set selection: sd-parse scoring ---

# Стат-паттерны: что считать "релевантным" под точку осей.
# В реальном Phase 1 это выводится из axis-point descriptor'а; в спайке — из эталона
# (какие статы доминируют в allocated-notables реального билда).
_STAT_PATTERNS: dict[str, list[re.Pattern]] = {
    "life":   [re.compile(r"maximum Life", re.I), re.compile(r"to Strength", re.I),
               re.compile(r"Recover.*Life", re.I)],
    "es":     [re.compile(r"maximum Energy Shield", re.I), re.compile(r"to Intelligence", re.I),
               re.compile(r"Recover.*Energy Shield", re.I)],
    "eva":    [re.compile(r"Evasion", re.I)],
    "armour": [re.compile(r"Armour", re.I)],
    "suppress":[re.compile(r"Suppress", re.I)],
    "resist": [re.compile(r"to all Elemental Resistances", re.I),
               re.compile(r"(Fire|Cold|Lightning) Resistance", re.I)],
    "ele_dmg":[re.compile(r"(Fire|Cold|Lightning) Damage", re.I), re.compile(r"Elemental Damage", re.I)],
    "crit":   [re.compile(r"Crit", re.I)],
    "aoe":    [re.compile(r"Area of Effect", re.I)],
    "dot":    [re.compile(r"Damage over Time", re.I), re.compile(r"(Ignite|Bleed|Poison)", re.I)],
    "speed":  [re.compile(r"(Cast|Attack) Speed", re.I), re.compile(r"Movement Speed", re.I)],
    "charge": [re.compile(r"(Frenzy|Power|Endurance) Charge", re.I)],
    "shock":  [re.compile(r"Shock", re.I)],
    "ignite": [re.compile(r"Ignite", re.I)],
}


def stat_tags(node: Node) -> set[str]:
    """Вернуть теги статов, которые узел даёт (по sd-строкам)."""
    text = " | ".join(node.sd)
    tags: set[str] = set()
    for tag, patterns in _STAT_PATTERNS.items():
        if any(p.search(text) for p in patterns):
            tags.add(tag)
    return tags


def score_node(node: Node, wanted: dict[str, float]) -> float:
    """Score узла = сумма весов wanted-тегов, которые он покрывает."""
    if not node.sd:
        return 0.0
    tags = stat_tags(node)
    return sum(wanted.get(t, 0.0) for t in tags)


def derive_wanted_from_etalon(graph: TreeGraph) -> dict[str, float]:
    """Вывести "нужные статы" из эталонного дерева: частота тегов в allocated notables.

    Это симулирует axis-point descriptor (в Phase 1 он будет входом, не выводом).
    Берём топ-N самых частых тегов среди эталонных notables — это "точка осей".
    """
    from collections import Counter
    alloc_notables = [graph.nodes[n] for n in graph.allocated
                      if n in graph.nodes and graph.nodes[n].type == "Notable"]
    c: Counter[str] = Counter()
    for nd in alloc_notables:
        for t in stat_tags(nd):
            c[t] += 1
    # нормализуем: каждый тег = доля notables, где он встречается
    total = max(1, len(alloc_notables))
    return {tag: cnt / total for tag, cnt in c.items()}


# --- Dijkstra-stitching ---

def dijkstra_to_targets(graph: TreeGraph, targets: set[str]) -> tuple[set[str], int]:
    """Кратчайшие пути (по числу аллоцируемых узлов) от class_start ко всем target-узлам.
    Возвращает (allocated_set, total_points). Normal/Notable/Socket/Keystone = cost 1.
    """
    if graph.class_start not in graph.nodes:
        raise RuntimeError(f"class_start {graph.class_start} не в графе")
    start = graph.class_start
    # multi-source Dijkstra от уже-достигнутых (стартовая точка = источник)
    dist: dict[str, int] = {start: 0}
    pq = [(0, start)]
    # нам нужны пути только к targets; расширяемся пока все targets не достигнуты
    remaining = set(targets)
    # стартовая точка класса — она всегда allocated, и если она target — убираем
    remaining.discard(start)
    while pq and remaining:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float("inf")):
            continue
        if u in remaining:
            remaining.discard(u)
            # не return — продолжаем, могут быть более короткие пути к другим
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

    # восстановить allocated-set: для каждого target — путь назад к start
    allocated: set[str] = {start}
    for t in targets:
        if t == start:
            continue
        if t not in dist:
            continue  # недостижим (аномалия)
        # backtrack от t по предкам с min-dist
        cur = t
        guard = 0
        while cur != start and guard < 5000:
            allocated.add(cur)
            cur_nd = graph.nodes.get(cur)
            if not cur_nd:
                break
            # найдём соседа с dist на point_cost меньше
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
            allocated.add(cur)  # start

    total_points = sum(graph.nodes[n].point_cost for n in allocated
                       if n in graph.nodes)
    return allocated, total_points


# --- Budget-prune ---

def prune_to_budget(graph: TreeGraph, targets: list[Node], wanted: dict[str, float],
                    budget: int) -> list[Node]:
    """Отбросить target-нотаблы с наименьшим score/point, пока дерево не уложится в бюджет.

    Грубо: считаем cost каждого target как длину пути от старта (Dijkstra per-target),
    жадно удаляем цели с min score/path-cost.
    """
    # cost каждого target = dist от start (независимый Dijkstra)
    # переиспользуем dijkstra_to_targets итеративно — но дешевле: один Dijkstra даёт все dist
    start = graph.class_start
    dist = _single_dijkstra_all(graph, start)
    scored = []
    for t in targets:
        d = dist.get(t.nid, 999)
        if d >= 999:
            continue
        sc = score_node(t, wanted)
        scored.append((sc / max(1, d), t.dn, t, d))  # dn как tie-breaker (строка)
    scored.sort(reverse=True, key=lambda x: (x[0], x[1]))

    # жадно набираем цели, пока суммарный cost (union путей) <= budget
    # грубая аппроксимация: суммируем независимые path-cost (реальный union <= суммы)
    kept: list[Node] = []
    running = 1  # стартовая точка
    for ratio, _dn, t, d in scored:
        if running + d <= budget:
            kept.append(t)
            running += d
    return kept


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
    """Кратчайший путь start->target (включая оба конца). Пусто если недостижим."""
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


def split_main_ascend(graph: TreeGraph) -> tuple[set[str], set[str]]:
    """Разделить эталонный alloc на main-tree и ascendancy."""
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


# --- Главная процедура спайка ---

def build_tree(graph: TreeGraph, budget_extra: int = 0,
               score_fn: Callable[[Node], float] | None = None) -> tuple[set[str], list[Node]]:
    """Построить дерево спайка. score_fn(node)->float переопределяет sd-scoring."""
    wanted = derive_wanted_from_etalon(graph) if score_fn is None else {}
    budget = graph.points_total + budget_extra

    all_targets = main_tree_notables(graph)
    if score_fn is not None:
        scored = [(score_fn(t), t.dn, t) for t in all_targets]
    else:
        scored = [(score_node(t, wanted), t.dn, t) for t in all_targets]
    scored.sort(reverse=True, key=lambda x: (x[0], x[1]))
    # берём топ-K (как в эталоне: ~17 notables + 2 keystones)
    etalon_n = len([n for n in graph.allocated
                    if n in graph.nodes and graph.nodes[n].type in ("Notable", "Keystone")])
    top_targets = [t for _, _dn, t in scored[:max(1, etalon_n)]]

    # prune под бюджет
    pruned = prune_to_budget(graph, top_targets, wanted, budget)
    target_ids = {t.nid for t in pruned}

    allocated, pts = dijkstra_to_targets(graph, target_ids)
    return allocated, pruned


def compare(allocated: set[str], graph: TreeGraph) -> dict:
    """Сравнить построенное дерево с эталоном."""
    etalon = graph.allocated
    our_notables = {n for n in allocated if n in graph.nodes
                    and graph.nodes[n].type in ("Notable", "Keystone")}
    etalon_notables = {n for n in etalon if n in graph.nodes
                       and graph.nodes[n].type in ("Notable", "Keystone")}
    overlap = our_notables & etalon_notables
    return {
        "etalon_notables_n": len(etalon_notables),
        "our_notables_n": len(our_notables),
        "overlap_n": len(overlap),
        "overlap_pct_of_etalon": round(len(overlap) / max(1, len(etalon_notables)) * 100, 1),
        "etalon_points": sum(graph.nodes[n].point_cost for n in etalon if n in graph.nodes),
        "our_points": sum(graph.nodes[n].point_cost for n in allocated if n in graph.nodes),
        "missed_etalon_notables": sorted(graph.nodes[n].dn for n in (etalon_notables - our_notables)
                                         if n in graph.nodes),
        "extra_our_notables": sorted(graph.nodes[n].dn for n in (our_notables - etalon_notables)
                                     if n in graph.nodes),
    }


def main(path: str = "builds/10.txt") -> None:
    import json
    code = open(path).read().strip()
    xml = pobcode.decode(code).decode("utf-8")
    pob = PobHeadless()
    pob.load_build_xml(xml)
    graph = load_tree_graph(pob)
    allocated, targets = build_tree(graph)
    print(json.dumps({
        "class": f"{graph.cur_class}/{graph.cur_ascend}",
        "budget_total": graph.points_total,
        "selected_targets": [t.dn for t in targets],
        "compare": compare(allocated, graph),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "builds/10.txt")
