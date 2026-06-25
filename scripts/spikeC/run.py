"""Батч-прогон спайка дерева (D14) по корпусу билдов.

Режимы:
  default   — sd-теги -> target-set + Dijkstra + prune
  --oracle  — эталонные notables как targets (тест stitching)
  --greedy  — iterative PoB-greedy tree build
  --hillclimb — greedy + swap/backtrack hill-climb
"""

from __future__ import annotations

import argparse
from pathlib import Path

from poebuildgen import pobcode
from poebuildgen.headless import PobHeadless
from poebuildgen.pool import WorkerPool

from scripts.spikeC.tree_build import build_tree, compare, dijkstra_to_targets, load_tree_graph
from scripts.spikeC.tree_marginals import (
    DPS_KEYS, greedy_tree_build, hillclimb_tree_build,
    measure_notable_marginals, pick_dps_key,
)
from poebuildgen.pool import _dps_from

_REPO = Path(__file__).resolve().parent.parent.parent


def run_one(path: str | Path, *, oracle: bool = False, marginal: bool = False,
            greedy: bool = False, hillclimb: bool = False,
            max_candidates: int = 80, workers: int | None = None) -> dict:
    path = Path(path)
    if not path.is_absolute():
        path = _REPO / path
    xml = pobcode.decode(path.read_text(encoding="utf-8").strip()).decode("utf-8")
    pob = PobHeadless()
    pob.load_build_xml(xml)
    graph = load_tree_graph(pob)

    if greedy or hillclimb or marginal:
        with WorkerPool(workers) as pool:
            ref = pool.map([{"xml": xml, "stats": list(DPS_KEYS)}])[0]
            prefer = pick_dps_key(ref.get("stats", {}))
            if greedy or hillclimb:
                if hillclimb:
                    allocated, history = hillclimb_tree_build(
                        xml, graph, pool, prefer=prefer, max_candidates=max_candidates)
                    mode = "hillclimb"
                else:
                    allocated, history = greedy_tree_build(
                        xml, graph, pool, prefer=prefer, max_candidates=max_candidates)
                    mode = "greedy"
                from scripts.spikeC.tree_xml import render_tree_nodes, parse_mastery_effects
                import xml.etree.ElementTree as ET
                spec = ET.fromstring(xml).find("Tree").findall("Spec")[0]
                m = parse_mastery_effects(spec)
                gxml = render_tree_nodes(xml, allocated, {n: e for n, e in m.items() if str(n) in allocated})
                gres = pool.map([{"xml": gxml, "stats": list(DPS_KEYS)}])[0]
                ref_dps = _dps_from(ref, prefer, DPS_KEYS)
                out_dps = _dps_from(gres, prefer, DPS_KEYS)
                extra = {"rounds": len(history), "ref_dps": ref_dps, "out_dps": out_dps,
                         "dps_pct": round(out_dps / ref_dps * 100, 1) if ref_dps else 0}
                if hillclimb:
                    extra["hc_moves"] = [h for h in history if "swap_" in str(h.get("round", ""))
                                         or "backtrack_" in str(h.get("round", ""))]
            else:
                base_dps, marg = measure_notable_marginals(
                    xml, graph, pool, prefer=prefer, max_candidates=max_candidates)
                allocated, targets = build_tree(graph, score_fn=lambda n, m=marg: m.get(n.nid, 0.0))
                mode = "marginal"
                extra = {"baseline_dps": base_dps, "marginals_n": len(marg),
                         "top_marg": [(graph.nodes[n].dn, round(v)) for n, v in
                                      sorted(marg.items(), key=lambda x: -x[1])[:5]]}
    elif oracle:
        targets = [graph.nodes[n] for n in graph.allocated
                   if n in graph.nodes and graph.nodes[n].type in ("Notable", "Keystone")]
        target_ids = {t.nid for t in targets}
        allocated, _ = dijkstra_to_targets(graph, target_ids)
        mode = "oracle"
        extra = {}
    else:
        allocated, targets = build_tree(graph)
        mode = "full"
        extra = {}

    target_ids = {n for n in allocated if n in graph.nodes
                  and graph.nodes[n].type in ("Notable", "Keystone")}
    cmp = compare(allocated, graph)
    return {
        "build": str(path.relative_to(_REPO)) if path.is_relative_to(_REPO) else str(path),
        "mode": mode,
        "class": f"{graph.cur_class}/{graph.cur_ascend}",
        "budget": graph.points_total,
        "targets_n": len(target_ids),
        **cmp,
        **extra,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("builds", nargs="*", default=["builds/2.txt", "builds/4.txt", "builds/6.txt",
                                                  "builds/8.txt", "builds/10.txt", "builds/11.txt"])
    ap.add_argument("--oracle", action="store_true")
    ap.add_argument("--marginal", action="store_true", help="single-shot PoB marginals -> target-set")
    ap.add_argument("--greedy", action="store_true", help="iterative PoB-greedy tree build")
    ap.add_argument("--hillclimb", action="store_true", help="greedy + swap/backtrack hill-climb")
    ap.add_argument("--max-candidates", type=int, default=80)
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    rows = [run_one(p, oracle=args.oracle, marginal=args.marginal, greedy=args.greedy,
                    hillclimb=args.hillclimb, max_candidates=args.max_candidates,
                    workers=args.workers)
            for p in args.builds]
    mode = ("hillclimb" if args.hillclimb else
            ("greedy" if args.greedy else ("marginal" if args.marginal else
             ("oracle" if args.oracle else "full"))))
    print(f"mode={mode}")
    print(f"{'build':<16} {'overlap%':>9} {'etalon':>7} {'ours':>5} {'pts_e':>6} {'pts_o':>6} {'dps%':>6}")
    for r in rows:
        dps_s = f"{r.get('dps_pct', 0):.0f}" if r.get("dps_pct") is not None else ""
        print(f"{r['build']:<16} {r['overlap_pct_of_etalon']:>8.1f}% "
              f"{r['etalon_notables_n']:>7} {r['our_notables_n']:>5} "
              f"{r['etalon_points']:>6} {r['our_points']:>6} {dps_s:>6}")
        if r.get("top_marg"):
            print(f"  top marg: {r['top_marg'][:3]}")
    verdict = "PASS" if all(r["overlap_pct_of_etalon"] >= 50 for r in rows) else (
        "BORDERLINE" if all(r["overlap_pct_of_etalon"] >= 30 for r in rows) else "FAIL")
    print(f"\nVERDICT ({verdict}): overlap>=50% PASS, >=30% BORDERLINE")


if __name__ == "__main__":
    main()
