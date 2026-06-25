import json
from pathlib import Path
from scripts.ml_v0.eval import compile_report

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "corpus" / "ml_v0"


def recompute_file(path: Path):
    if not path.exists():
        print(f"File {path} does not exist. Skipping.")
        return
    
    print(f"Recomputing report for: {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    
    # Manifest default values for delta thresholds
    manifest_path = OUT_DIR / "manifest.json"
    dps_delta = 15.0
    ovl_delta = 10.0
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        gate = manifest.get("gate", {})
        dps_delta = gate.get("dps_delta_pp", 15.0)
        ovl_delta = gate.get("overlap_delta_pp", 10.0)

    report = compile_report(rows, dps_delta=dps_delta, ovl_delta=ovl_delta)
    
    # Preserve key meta fields if they existed
    for key in ["holdout", "n_builds"]:
        if key in data:
            report[key] = data[key]
            
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Successfully updated {path.name}.")


def main():
    recompute_file(OUT_DIR / "eval_ninja.json")
    recompute_file(OUT_DIR / "eval_report.json")


if __name__ == "__main__":
    main()
