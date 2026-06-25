"""Build ML-v0 dataset from corpus/ + holdout manifest."""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from scripts.ml_v0.axis import (
    build_features_from_meta,
    build_features_from_xml,
    build_gem_vocab,
    get_reference_graph,
    load_xml,
    notable_labels_from_xml,
    notable_labels_from_xml_fast,
)
from poebuildgen.headless import PobHeadless
from scripts.spikeC.tree_build import load_tree_graph

_REPO = Path(__file__).resolve().parents[2]
CORPUS = _REPO / "corpus"
OUT_DIR = _REPO / "corpus" / "ml_v0"
GOLD_BUILDS = [f"builds/{i}.txt" for i in range(1, 12)]
NINJA_HOLDOUT = 9
SEED = 42


def _pick_ninja_holdout(metas: list[Path], n: int) -> set[str]:
    """Stratified by ascendancy: ~1 per top ascendancy where possible."""
    by_asc: dict[str, list[str]] = defaultdict(list)
    for p in metas:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        bid = p.name.replace(".meta.json", "")
        by_asc[str(d.get("ascendancy") or "?")].append(bid)
    rng = random.Random(SEED)
    picked: set[str] = set()
    keys = sorted(by_asc.keys(), key=lambda k: -len(by_asc[k]))
    while len(picked) < n and keys:
        for k in keys:
            if len(picked) >= n:
                break
            pool = [b for b in by_asc[k] if b not in picked]
            if pool:
                picked.add(rng.choice(pool))
        keys = [k for k in keys if any(b not in picked for b in by_asc[k])]
    return picked


def prepare(*, ninja_holdout: int = NINJA_HOLDOUT) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gem_vocab = build_gem_vocab(CORPUS)
    metas = sorted(CORPUS.glob("*.meta.json"))
    holdout_ninja = _pick_ninja_holdout(metas, ninja_holdout)
    graph = get_reference_graph()

    records: list[dict] = []
    notable_freq: Counter[str] = Counter()

    for mp in metas:
        bid = mp.name.replace(".meta.json", "")
        xml_path = mp.with_name(f"{bid}.pob.xml")
        if not xml_path.exists():
            continue
        meta = json.loads(mp.read_text(encoding="utf-8"))
        xml = load_xml(xml_path)
        labels = notable_labels_from_xml_fast(xml, graph)
        for nid in labels:
            notable_freq[nid] += 1
        feats = build_features_from_meta(meta, gem_vocab=gem_vocab)
        records.append({
            "id": bid,
            "source": "ninja",
            "holdout": bid in holdout_ninja,
            "features": feats,
            "notable_ids": sorted(labels),
            "class": meta.get("class"),
            "ascendancy": meta.get("ascendancy"),
            "main_skill": feats.get("main_skill"),
        })

    # Gold builds (always holdout, not in train)
    for rel in GOLD_BUILDS:
        p = _REPO / rel
        if not p.exists():
            continue
        xml = load_xml(p)
        labels = notable_labels_from_xml_fast(xml, graph)
        pob = PobHeadless()
        pob.load_build_xml(xml)
        g = load_tree_graph(pob)
        feats = build_features_from_xml(xml, g, gem_vocab=gem_vocab)
        records.append({
            "id": rel.replace("/", "_").replace(".txt", ""),
            "source": "gold",
            "holdout": True,
            "features": feats,
            "notable_ids": sorted(labels),
            "class": g.cur_class,
            "ascendancy": g.cur_ascend,
            "main_skill": feats.get("main_skill"),
            "gold_path": rel,
        })

    feat_cols = sorted({k for r in records for k in r["features"] if k != "main_skill"})
    # notable vocab: train only, freq >= 2
    train_ids = {r["id"] for r in records if not r["holdout"]}
    train_notables = Counter()
    for r in records:
        if r["id"] in train_ids:
            for nid in r["notable_ids"]:
                train_notables[nid] += 1
    notable_vocab = [nid for nid, c in train_notables.most_common() if c >= 2]

    manifest = {
        "gem_vocab": gem_vocab,
        "feature_columns": feat_cols,
        "notable_vocab": notable_vocab,
        "n_records": len(records),
        "n_train": sum(1 for r in records if not r["holdout"]),
        "n_holdout": sum(1 for r in records if r["holdout"]),
        "ninja_holdout_ids": sorted(holdout_ninja),
        "gate": {
            "primary": "dps_pct",
            "secondary": "overlap_pct",
            "dps_delta_pp": 15,
            "overlap_delta_pp": 10,
        },
    }

    (OUT_DIR / "ml_records.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    m = prepare()
    print(json.dumps(m, indent=2, ensure_ascii=False))
