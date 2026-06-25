"""ML-v0 CLI: prepare | train | eval | all."""

from __future__ import annotations

import argparse
import json


def main():
    ap = argparse.ArgumentParser(description="ML-v0 tree target-set")
    ap.add_argument("cmd", choices=["prepare", "train", "eval", "all"])
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    if args.cmd in ("prepare", "all"):
        from scripts.ml_v0.prepare import prepare
        m = prepare()
        print("prepare:", json.dumps({k: m[k] for k in ("n_records", "n_train", "n_holdout")}))

    if args.cmd in ("train", "all"):
        from scripts.ml_v0.train import train
        p = train()
        print("train:", p)

    if args.cmd in ("eval", "all"):
        from scripts.ml_v0.eval import eval_holdout
        r = eval_holdout(workers=args.workers)
        print(f"mode=ml_v0_eval verdict={r['verdict']}")
        print(f"{'build':<16} {'ml_ovl':>7} {'hc_ovl':>7} {'ml_dps%':>8} {'hc_dps%':>8} {'ml_pts':>7} {'hc_pts':>7} {'budget':>7}")
        for row in r["rows"]:
            print(f"{row['build']:<16} {row['ml_overlap']:>6.1f}% {row['hc_overlap']:>6.1f}% "
                  f"{row['ml_dps_pct']:>7.1f} {row['hc_dps_pct']:>7.1f} "
                  f"{row['ml_points']:>7} {row['hc_points']:>7} {row['budget_points']:>7}")
        print(f"\navg ml_dps={r['avg']['ml_dps_pct']}% hc_dps={r['avg']['hc_dps_pct']}% "
              f"| gate dps={r['gate']['dps_primary']} overlap={r['gate']['overlap_secondary']}")


if __name__ == "__main__":
    main()
