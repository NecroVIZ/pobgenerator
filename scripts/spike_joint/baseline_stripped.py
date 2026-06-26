"""Baseline: эталонное дерево + stripped gear → DPS (для нормализации Phase 2 gate).

Сохраняет corpus/ml_v0/joint_baseline_stripped.json
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from poebuildgen import pobcode
from poebuildgen.headless import PobHeadless

from scripts.spikeB.gear_opt import DPS_KEYS, pick_dps_key
from scripts.spikeB.harness import Build
from scripts.spikeB.engine import PoolEngine
from scripts.spikeC.tree_build import load_tree_graph
from scripts.spikeC.tree_xml import parse_mastery_effects, render_tree_nodes
from scripts.spike_joint.run import combined_xml, mastery_subset

_REPO = Path(__file__).resolve().parent.parent.parent
_DEFAULT_BUILDS = ["builds/10.txt", "builds/8.txt", "builds/2.txt"]
_OUT = _REPO / "corpus" / "ml_v0" / "joint_baseline_stripped.json"


def etalon_stripped_dps(path: Path, *, workers: int | None = None) -> dict:
    xml = pobcode.decode(path.read_text(encoding="utf-8").strip()).decode("utf-8")
    b_ref = Build.from_xml(xml, path)
    etalon_mastery = parse_mastery_effects(ET.fromstring(xml).find("Tree").findall("Spec")[0])

    pob = PobHeadless()
    pob.load_build_xml(xml)
    graph = load_tree_graph(pob)
    tree_alloc = set(graph.allocated)

    opt_slots = b_ref.rare_core_slots()
    gear_ov = {s: b_ref.item_for_slot(s).stripped() for s in opt_slots}

    eng = PoolEngine(workers)
    eng.prefer = pick_dps_key(eng.stats(b_ref.xml, list(DPS_KEYS)))

    try:
        ref_dps = float(eng.stats(b_ref.xml, [eng.prefer]).get(eng.prefer) or 0)
        cx = combined_xml(b_ref, tree_alloc, gear_ov, etalon_mastery)
        stripped_dps = float(eng.stats(cx, [eng.prefer]).get(eng.prefer) or 0)
    finally:
        eng.close()

    pct = round(stripped_dps / ref_dps * 100, 2) if ref_dps else 0.0
    return {
        "build": str(path.relative_to(_REPO)) if path.is_relative_to(_REPO) else str(path),
        "ref_dps": ref_dps,
        "etalon_stripped_dps": stripped_dps,
        "etalon_stripped_pct": pct,
        "prefer": eng.prefer,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Etalon tree + stripped gear baseline DPS")
    ap.add_argument("builds", nargs="*", default=_DEFAULT_BUILDS)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--out", type=str, default=str(_OUT))
    args = ap.parse_args()

    rows = []
    for p in args.builds:
        path = Path(p)
        if not path.is_absolute():
            path = _REPO / path
        print(f"== {path.name} ==")
        row = etalon_stripped_dps(path, workers=args.workers)
        print(f"  ref={row['ref_dps']:,.0f}  etalon_stripped={row['etalon_stripped_dps']:,.0f} "
              f"({row['etalon_stripped_pct']}%)")
        rows.append(row)

    out = Path(args.out)
    if not out.is_absolute():
        out = _REPO / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
