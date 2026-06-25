"""Train ML-v0 notable classifier (build features + notable_id -> in-tree?)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np

from scripts.ml_v0.axis import feature_vector

_REPO = Path(__file__).resolve().parents[2]
OUT_DIR = _REPO / "corpus" / "ml_v0"
SEED = 42
NEG_PER_POS = 4


def _load_records() -> tuple[list[dict], dict]:
    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    records = []
    for line in (OUT_DIR / "ml_records.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records, manifest


def _build_rows(records: list[dict], manifest: dict, *, train_only: bool) -> tuple[np.ndarray, np.ndarray, list[str]]:
    cols = manifest["feature_columns"]
    vocab = manifest["notable_vocab"]
    rng = random.Random(SEED)
    rows_x: list[list] = []
    rows_y: list[int] = []
    row_nids: list[str] = []

    pool = [r for r in records if (not train_only or not r["holdout"])]
    for rec in pool:
        labels = set(rec["notable_ids"])
        positives = [nid for nid in labels if nid in vocab]
        for nid in positives:
            feats = feature_vector(rec["features"], cols)
            rows_x.append(feats + [nid])
            rows_y.append(1)
            row_nids.append(nid)
        for _ in range(NEG_PER_POS * max(1, len(positives))):
            cand = rng.choice(vocab)
            if cand in labels:
                continue
            feats = feature_vector(rec["features"], cols)
            rows_x.append(feats + [cand])
            rows_y.append(0)
            row_nids.append(cand)

    cat_cols = cols + ["notable_id"]
    # encode notable_id as string for catboost
    X = np.array(rows_x, dtype=object)
    y = np.array(rows_y, dtype=np.int32)
    return X, y, cat_cols


def train() -> Path:
    records, manifest = _load_records()
    X, y, cat_cols = _build_rows(records, manifest, train_only=True)
    model_path = OUT_DIR / "model.cbm"
    meta_path = OUT_DIR / "train_meta.json"

    try:
        from catboost import CatBoostClassifier, Pool
        cat_idx = [len(cat_cols) - 1]
        # CatBoost needs numeric + cat; convert build features to float
        Xf = X.copy()
        for i in range(Xf.shape[0]):
            for j in range(len(cat_cols) - 1):
                Xf[i, j] = float(Xf[i, j])
        pool = Pool(Xf, y, cat_features=cat_idx)
        model = CatBoostClassifier(
            iterations=300,
            depth=6,
            learning_rate=0.08,
            loss_function="Logloss",
            random_seed=SEED,
            verbose=False,
        )
        model.fit(pool)
        model.save_model(str(model_path))
        backend = "catboost"
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.preprocessing import LabelEncoder
        le = LabelEncoder()
        nids = X[:, -1]
        nid_enc = le.fit_transform(nids)
        Xn = np.column_stack([X[:, :-1].astype(float), nid_enc])
        model = HistGradientBoostingClassifier(max_depth=8, max_iter=200, random_state=SEED)
        model.fit(Xn, y)
        import joblib
        model_path = OUT_DIR / "model.joblib"
        joblib.dump({"model": model, "le": le}, model_path)
        backend = "sklearn"

    meta = {
        "backend": backend,
        "model_path": model_path.name,
        "n_rows": int(len(y)),
        "pos_rate": float(y.mean()),
        "feature_columns": manifest["feature_columns"],
        "notable_vocab": manifest["notable_vocab"],
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return model_path


if __name__ == "__main__":
    p = train()
    print(f"saved {p}")
