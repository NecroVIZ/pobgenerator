"""Обход 10-минутного ceiling background-task: гоняем ninja-holdout по 2 билда за вызов,
накапливаем partial rows в JSON, потом combine в финальный eval_ninja.json.

Использование:
  python -m scripts.ml_v0.eval_ninja_chunks run 0   # билды 0,1
  python -m scripts.ml_v0.eval_ninja_chunks run 1   # билды 2,3
  ...
  python -m scripts.ml_v0.eval_ninja_chunks combine # собрать финальный отчёт
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.ml_v0.eval import eval_holdout, _load_model, _eval_one, OUT_DIR, _REPO
from poebuildgen.pool import WorkerPool

PARTIAL = OUT_DIR / "ninja_partials"
PARTIAL.mkdir(parents=True, exist_ok=True)
CHUNK = 1  # билдов за один вызов (ninja-builds тяжёлые, >5мин/шт; ceiling 10мин)


def cmd_run(chunk_idx: int) -> None:
    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    ids = manifest["ninja_holdout_ids"]
    start = chunk_idx * CHUNK
    end = min(start + CHUNK, len(ids))
    if start >= len(ids):
        print(f"chunk {chunk_idx}: out of range (total {len(ids)})")
        return
    chunk_ids = ids[start:end]
    paths = [str(_REPO / "corpus" / f"{bid}.pob.xml") for bid in chunk_ids]
    def safe_name(path_str: str) -> str:
        return Path(path_str).name.encode('ascii', errors='replace').decode('ascii')

    print(f"chunk {chunk_idx}: builds {start}..{end-1} ({len(paths)} builds)")
    for p in paths:
        print(f"  {safe_name(p)}")

    model_pack, meta, backend = _load_model()
    rows = []
    with WorkerPool(4) as pool:
        for p in paths:
            print(f"  evaluating {safe_name(p)} ...", flush=True)
            row = _eval_one(p, pool, model_pack, meta, backend, lambda_blend=0.5)
            rows.append(row)
            print(f"    ML={row['ml_dps_pct']}% HC={row['hc_dps_pct']}% "
                  f"ovl_ML={row['ml_overlap']} ovl_HC={row['hc_overlap']}", flush=True)

    out = PARTIAL / f"chunk_{chunk_idx:02d}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {out} ({len(rows)} rows)")


def cmd_combine() -> None:
    all_rows = []
    for f in sorted(PARTIAL.glob("chunk_*.json")):
        rows = json.loads(f.read_text(encoding="utf-8"))
        all_rows.extend(rows)
    if not all_rows:
        print("no partial chunks found")
        return
    print(f"combining {len(all_rows)} rows from {len(list(PARTIAL.glob('chunk_*.json')))} chunks")

    # пересчёт avg + robustness (копия логики eval.py, но на accumulated rows)
    ml_dps = [r["ml_dps_pct"] for r in all_rows]
    hc_dps = [r["hc_dps_pct"] for r in all_rows]
    ml_ovl = [r["ml_overlap"] for r in all_rows]
    hc_ovl = [r["hc_overlap"] for r in all_rows]
    deltas = sorted(r["ml_dps_pct"] - r["hc_dps_pct"] for r in all_rows)
    n = len(deltas)
    median = deltas[n // 2] if n % 2 else (deltas[n // 2 - 1] + deltas[n // 2]) / 2
    wins = sum(1 for d in deltas if d > 0.05)
    losses = sum(1 for d in deltas if d < -0.05)
    ties = n - wins - losses
    excl = [d for d in deltas if abs(d) <= 20]
    avg_excl = sum(excl) / len(excl) if excl else 0

    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    gate_cfg = manifest.get("gate", {})
    dps_thresh = gate_cfg.get("dps_primary_pp", 15)
    ovl_thresh = gate_cfg.get("overlap_secondary_pp", 10)
    floor_pp = gate_cfg.get("per_build_dps_floor", 5)

    avg_ml_dps = sum(ml_dps) / n
    avg_hc_dps = sum(hc_dps) / n
    avg_ml_ovl = sum(ml_ovl) / n
    avg_hc_ovl = sum(hc_ovl) / n

    dps_pass = (avg_ml_dps - avg_hc_dps) >= dps_thresh
    ovl_pass = (avg_ml_ovl - avg_hc_ovl) >= ovl_thresh
    per_build_pass = all(r["ml_dps_pct"] >= r["hc_dps_pct"] - floor_pp for r in all_rows)
    overall = (dps_pass or ovl_pass) and per_build_pass

    report = {
        "rows": all_rows,
        "avg": {"ml_dps_pct": round(avg_ml_dps, 1), "hc_dps_pct": round(avg_hc_dps, 1),
                "ml_overlap": round(avg_ml_ovl, 1), "hc_overlap": round(avg_hc_ovl, 1)},
        "robustness": {"median_dps_delta": round(median, 1), "wins": wins, "losses": losses,
                       "ties": ties, "avg_delta_excl_outliers": round(avg_excl, 1)},
        "gate": {"dps_primary": dps_pass, "overlap_secondary": ovl_pass,
                 "per_build_dps_floor": per_build_pass},
        "verdict": "PASS" if overall else "FAIL",
        "holdout": "ninja",
        "n_builds": n,
    }
    out = OUT_DIR / "eval_ninja.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== FINAL ninja eval ({n} builds) ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: eval_ninja_chunks run <idx> | combine")
        return
    if sys.argv[1] == "run":
        cmd_run(int(sys.argv[2]))
    elif sys.argv[1] == "combine":
        cmd_combine()
    else:
        print(f"unknown cmd: {sys.argv[1]}")


if __name__ == "__main__":
    main()
