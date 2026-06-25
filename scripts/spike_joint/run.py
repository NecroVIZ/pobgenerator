"""Joint-спайк (D1): fixpoint дерево <-> шмот через PoB-оракул.

Цикл (как задумано в DESIGN-v2):
  stripped gear + minimal tree (ascend + class start)
  -> [greedy tree rounds] -> [CP-SAT + hybrid gear] -> repeat

Метрика: joint DPS / reference DPS (полный экспертный билд).
"""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

from poebuildgen import pobcode
from poebuildgen.headless import PobHeadless
from poebuildgen.pool import WorkerPool, _dps_from

from scripts.spikeB.gear_opt import DPS_KEYS, optimize_gear, pick_dps_key
from scripts.spikeB.harness import Build
from scripts.spikeB.engine import MetaEngine, PoolEngine
from scripts.spikeC.tree_build import load_tree_graph, split_main_ascend
from scripts.spikeC.tree_marginals import greedy_tree_build
from scripts.spikeC.tree_xml import parse_mastery_effects, render_tree_nodes

_REPO = Path(__file__).resolve().parent.parent.parent


def mastery_subset(etalon: dict[int, int], alloc: set[str]) -> dict[int, int]:
    return {n: e for n, e in etalon.items() if str(n) in alloc}


def combined_xml(build: Build, tree_alloc: set[str], gear_ov: dict[str, str],
                 etalon_mastery: dict[int, int]) -> str:
    gxml = build.render(gear_ov)
    return render_tree_nodes(gxml, tree_alloc, mastery_subset(etalon_mastery, tree_alloc))


def joint_fixpoint(
    path: Path,
    *,
    gear_start: str = "expert",
    tree_start: str = "minimal",
    joint_iters: int = 2,
    tree_rounds: int = 25,
    tree_candidates: int = 35,
    bis_evals: int = 250,
    life_frac: float = 0.6,
    tree_only: bool = False,
    workers: int | None = None,
) -> dict:
    xml = pobcode.decode(path.read_text(encoding="utf-8").strip()).decode("utf-8")
    b_ref = Build.from_xml(xml, path)
    etalon_mastery = parse_mastery_effects(ET.fromstring(xml).find("Tree").findall("Spec")[0])

    pob = PobHeadless()
    pob.load_build_xml(xml)
    graph = load_tree_graph(pob)
    _, ascend = split_main_ascend(graph)

    opt_slots = b_ref.rare_core_slots()
    if gear_start == "expert":
        gear_ov = {s: (b_ref.by_id[b_ref.slot_to_id[s]].text or "")
                   for s in opt_slots}
    else:
        gear_ov = {s: b_ref.item_for_slot(s).stripped() for s in opt_slots}

    eng = PoolEngine(workers)
    meta = MetaEngine()
    eng.prefer = pick_dps_key(eng.stats(b_ref.xml, list(DPS_KEYS)))

    if tree_start == "expert":
        tree_alloc = set(graph.allocated)
    elif tree_start == "ml":
        from scripts.ml_v0.eval import _load_model, predict_tree_alloc
        model, meta_info, backend = _load_model()
        tree_alloc = predict_tree_alloc(
            xml, model, meta_info, backend, eng.pool,
            lambda_blend=0.5, prefer=eng.prefer
        )
    else:
        tree_alloc = set(ascend) | {graph.class_start}

    ref_dps = float(eng.stats(b_ref.xml, [eng.prefer]).get(eng.prefer) or 0)
    history: list[dict] = []
    evals = 0

    try:
        for k in range(joint_iters):
            cx = combined_xml(b_ref, tree_alloc, gear_ov, etalon_mastery)
            b_cur = Build.from_xml(cx, path)

            # TREE phase
            with WorkerPool(workers) as pool:
                tree_alloc, thist = greedy_tree_build(
                    cx, graph, pool, prefer=eng.prefer,
                    max_rounds=tree_rounds, max_candidates=tree_candidates,
                )
            cx = combined_xml(b_ref, tree_alloc, gear_ov, etalon_mastery)
            b_cur = Build.from_xml(cx, path)

            # GEAR phase (пропуск в tree-only)
            if not tree_only:
                g_dps, gear_ov = optimize_gear(
                    eng, meta, b_cur, b_ref, opt_slots, eng.prefer, gear_ov,
                    life_frac=life_frac, fixpoint_iters=2, bis_evals=bis_evals)

            cx = combined_xml(b_ref, tree_alloc, gear_ov, etalon_mastery)
            final = eng.stats(cx, [eng.prefer, "Life"])
            dps = float(final.get(eng.prefer) or 0)
            history.append({
                "iter": k,
                "tree_rounds": len(thist),
                "tree_pts": sum(graph.nodes[n].point_cost for n in tree_alloc if n in graph.nodes),
                "dps": dps,
                "life": final.get("Life"),
            })
            print(f"  joint iter {k}: tree+{len(thist)} notables pts="
                  f"{history[-1]['tree_pts']} PoB-DPS={dps:,.0f} ({dps/ref_dps*100:.1f}% ref)")

        final_xml = combined_xml(b_ref, tree_alloc, gear_ov, etalon_mastery)
        final_dps = float(eng.stats(final_xml, [eng.prefer]).get(eng.prefer) or 0)
        evals = eng.evals
    finally:
        eng.close()

    from scripts.spikeC.tree_build import compare
    cmp = compare(tree_alloc, graph)

    return {
        "build": str(path.relative_to(_REPO)) if path.is_relative_to(_REPO) else str(path),
        "gear_start": gear_start,
        "tree_start": tree_start,
        "ref_dps": ref_dps,
        "joint_dps": final_dps,
        "dps_pct": round(final_dps / ref_dps * 100, 1) if ref_dps else 0,
        "tree_overlap_pct": cmp["overlap_pct_of_etalon"],
        "history": history,
        "evals": evals,
    }


def main():
    ap = argparse.ArgumentParser(description="Joint spike: tree <-> gear fixpoint")
    ap.add_argument("builds", nargs="*", default=["builds/10.txt", "builds/8.txt", "builds/2.txt"])
    ap.add_argument("--gear-start", choices=("expert", "stripped"), default="expert")
    ap.add_argument("--tree-start", choices=("minimal", "expert", "ml"), default="ml")
    ap.add_argument("--joint-iters", type=int, default=2)
    ap.add_argument("--tree-rounds", type=int, default=25)
    ap.add_argument("--tree-candidates", type=int, default=30)
    ap.add_argument("--bis-evals", type=int, default=250)
    ap.add_argument("--tree-only", action="store_true", help="только greedy-дерево, шмот не трогаем")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    rows = []
    for p in args.builds:
        path = Path(p)
        if not path.is_absolute():
            path = _REPO / path
        print(f"\n== {path.name} ==")
        rows.append(joint_fixpoint(
            path, gear_start=args.gear_start, tree_start=args.tree_start,
            joint_iters=args.joint_iters, tree_rounds=args.tree_rounds,
            tree_candidates=args.tree_candidates, bis_evals=args.bis_evals,
            tree_only=args.tree_only, workers=args.workers))

    mode = f"gear={args.gear_start} tree={args.tree_start}"
    if args.tree_only:
        mode += " tree-only"
    print(f"\nmode: {mode}")
    print(f"{'build':<16} {'dps%':>7} {'tree_ov%':>9} {'ref_dps':>14} {'joint':>14}")
    for r in rows:
        print(f"{r['build']:<16} {r['dps_pct']:>6.1f}% {r['tree_overlap_pct']:>8.1f}% "
              f"{r['ref_dps']:>14,.0f} {r['joint_dps']:>14,.0f}")
    ok = sum(1 for r in rows if r["dps_pct"] >= 60)
    print(f"\nVERDICT: {ok}/{len(rows)} builds >=60% ref DPS  "
          f"({'PASS' if ok >= len(rows) * 0.67 else 'BORDERLINE' if ok else 'FAIL'})")


if __name__ == "__main__":
    main()
