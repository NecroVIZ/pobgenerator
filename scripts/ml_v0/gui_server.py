"""Real-time ML-v0 training dashboard server (FastAPI + WebSocket)."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from starlette.websockets import WebSocketState

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
app = FastAPI(title="ML-v0 Dashboard")
_clients: list[WebSocket] = []
_state: dict[str, Any] = {
    "phase": "idle",       # idle | preparing | training | evaluating | done | error
    "progress": 0,         # 0-100
    "train_iters": [],     # [{iter, loss, elapsed}]
    "eval_rows": [],       # per-build eval results
    "eval_report": None,   # final report
    "logs": [],            # text log lines
    "error": None,
}
_lock = threading.Lock()

log = logging.getLogger("ml_v0_gui")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


# ---------------------------------------------------------------------------
# Broadcast helpers
# ---------------------------------------------------------------------------
async def _broadcast(msg: dict):
    """Send JSON message to every connected WebSocket client."""
    data = json.dumps(msg, ensure_ascii=False, default=str)
    stale: list[WebSocket] = []
    for ws in _clients:
        try:
            if ws.client_state == WebSocketState.CONNECTED:
                await ws.send_text(data)
        except Exception:
            stale.append(ws)
    for ws in stale:
        if ws in _clients:
            _clients.remove(ws)


def _sync_broadcast(msg: dict):
    """Thread-safe broadcast from sync code (training thread)."""
    loop = _get_loop()
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(_broadcast(msg), loop)


_event_loop = None


def _get_loop():
    return _event_loop


def _emit_log(text: str):
    with _lock:
        _state["logs"].append(text)
    log.info(text)
    _sync_broadcast({"type": "log", "text": text, "ts": time.time()})


def _set_phase(phase: str, progress: int = 0):
    with _lock:
        _state["phase"] = phase
        _state["progress"] = progress
    _sync_broadcast({"type": "phase", "phase": phase, "progress": progress})
    _emit_log(f"▶ Phase: {phase}")


# ---------------------------------------------------------------------------
# CatBoost live callback
# ---------------------------------------------------------------------------
class _WsCallback:
    """CatBoost-compatible callback: sends per-iteration metrics to WS."""

    def __init__(self):
        self.start_time = time.time()

    def after_iteration(self, info):
        iteration = info.iteration
        metrics = info.metrics or {}
        # CatBoost stores learn metrics as {'learn': {'Logloss': val}}
        loss = None
        if "learn" in metrics:
            for k, v in metrics["learn"].items():
                loss = v
                break
        elif "validation" in metrics:
            for k, v in metrics["validation"].items():
                loss = v
                break

        elapsed = round(time.time() - self.start_time, 2)
        row = {"iter": iteration, "loss": loss, "elapsed": elapsed}
        with _lock:
            _state["train_iters"].append(row)
            total_iters = _state.get("total_iters", 300)
            _state["progress"] = min(95, int(iteration / total_iters * 100))
        _sync_broadcast({
            "type": "train_iter",
            "iter": iteration,
            "loss": loss,
            "elapsed": elapsed,
            "progress": _state["progress"],
        })
        return True  # continue training


# ---------------------------------------------------------------------------
# Pipeline runner (sync, runs in background thread)
# ---------------------------------------------------------------------------
def _run_pipeline(cmd: str, workers: int, lambda_blend: float, use_ninja: bool):
    try:
        if cmd in ("prepare", "all"):
            _set_phase("preparing", 5)
            from scripts.ml_v0.prepare import prepare
            m = prepare()
            _emit_log(f"Prepare done: {m['n_records']} records, {m['n_train']} train, {m['n_holdout']} holdout")
            with _lock:
                _state["progress"] = 30

        if cmd in ("train", "all"):
            _set_phase("training", 35)
            _train_with_ws(total_iters=300)

        if cmd in ("eval", "all"):
            _set_phase("evaluating", 70)
            _eval_with_ws(workers=workers, lambda_blend=lambda_blend, use_ninja=use_ninja)

        _set_phase("done", 100)
        _emit_log("✅ Pipeline finished!")

    except Exception as e:
        tb = traceback.format_exc()
        _emit_log(f"❌ ERROR: {e}\n{tb}")
        with _lock:
            _state["phase"] = "error"
            _state["error"] = str(e)
        _sync_broadcast({"type": "error", "error": str(e), "traceback": tb})


def _train_with_ws(total_iters: int = 300):
    """Run training with WebSocket-streamed iteration metrics."""
    from scripts.ml_v0.train import _load_records, _build_rows, OUT_DIR, SEED

    records, manifest = _load_records()
    X, y, cat_cols = _build_rows(records, manifest, train_only=True)
    model_path = OUT_DIR / "model.cbm"
    meta_path = OUT_DIR / "train_meta.json"

    with _lock:
        _state["train_iters"] = []
        _state["total_iters"] = total_iters

    _emit_log(f"Training: {X.shape[0]} rows, {X.shape[1]} features")

    try:
        from catboost import CatBoostClassifier, Pool
        import numpy as np

        cat_idx = [len(cat_cols) - 1]
        Xf = X.copy()
        for i in range(Xf.shape[0]):
            for j in range(len(cat_cols) - 1):
                Xf[i, j] = float(Xf[i, j])
        pool = Pool(Xf, y, cat_features=cat_idx)

        cb = _WsCallback()
        model = CatBoostClassifier(
            iterations=total_iters,
            depth=6,
            learning_rate=0.08,
            loss_function="Logloss",
            random_seed=SEED,
            verbose=False,
        )
        model.fit(pool, callbacks=[cb])
        model.save_model(str(model_path))
        backend = "catboost"
        _emit_log(f"CatBoost model saved → {model_path.name}")

    except ImportError:
        _emit_log("CatBoost not found, falling back to sklearn")
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.preprocessing import LabelEncoder
        import numpy as np
        import joblib

        le = LabelEncoder()
        nids = X[:, -1]
        nid_enc = le.fit_transform(nids)
        Xn = np.column_stack([X[:, :-1].astype(float), nid_enc])
        model = HistGradientBoostingClassifier(max_depth=8, max_iter=200, random_state=SEED)

        _emit_log("sklearn HistGradientBoosting: training (no per-iter feedback)…")
        _sync_broadcast({"type": "train_iter", "iter": 0, "loss": None, "elapsed": 0, "progress": 50})
        model.fit(Xn, y)

        model_path = OUT_DIR / "model.joblib"
        joblib.dump({"model": model, "le": le}, model_path)
        backend = "sklearn"
        _emit_log(f"sklearn model saved → {model_path.name}")

    import numpy as np
    meta = {
        "backend": backend,
        "model_path": model_path.name,
        "n_rows": int(len(y)),
        "pos_rate": float(y.mean()),
        "feature_columns": manifest["feature_columns"],
        "notable_vocab": manifest["notable_vocab"],
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _emit_log(f"Train meta: {meta['n_rows']} rows, pos_rate={meta['pos_rate']:.3f}, backend={backend}")


def _eval_with_ws(workers: int = 4, lambda_blend: float = 0.5, use_ninja: bool = False):
    """Run eval and stream each build result individually."""
    from scripts.ml_v0.eval import _load_model, _eval_one, GOLD_EVAL, OUT_DIR
    from poebuildgen.pool import WorkerPool

    model_pack, meta, backend = _load_model()

    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    if use_ninja:
        paths = [str(_REPO / "corpus" / f"{bid}.pob.xml") for bid in manifest["ninja_holdout_ids"]]
    else:
        paths = GOLD_EVAL

    _emit_log(f"Evaluating {len(paths)} builds (workers={workers}, λ={lambda_blend})…")

    with _lock:
        _state["eval_rows"] = []

    rows = []
    with WorkerPool(workers) as pool:
        for i, p in enumerate(paths):
            _emit_log(f"  eval [{i+1}/{len(paths)}]: {p}")
            row = _eval_one(p, pool, model_pack, meta, backend, lambda_blend=lambda_blend)
            rows.append(row)
            with _lock:
                _state["eval_rows"].append(row)
                _state["progress"] = 70 + int((i + 1) / len(paths) * 25)
            _sync_broadcast({
                "type": "eval_row",
                "index": i,
                "total": len(paths),
                "row": row,
                "progress": _state["progress"],
            })

    # Build final report (same logic as eval.py eval_holdout)
    gate = manifest.get("gate", {})
    dps_delta = gate.get("dps_delta_pp", 15)
    ovl_delta = gate.get("overlap_delta_pp", 10)

    ml_dps_avg = sum(r["ml_dps_pct"] for r in rows) / max(1, len(rows))
    hc_dps_avg = sum(r["hc_dps_pct"] for r in rows) / max(1, len(rows))
    ml_ovl_avg = sum(r["ml_overlap"] for r in rows) / max(1, len(rows))
    hc_ovl_avg = sum(r["hc_overlap"] for r in rows) / max(1, len(rows))

    dps_pass = ml_dps_avg >= hc_dps_avg + dps_delta
    ovl_pass = ml_ovl_avg >= hc_ovl_avg + ovl_delta
    per_build_dps = all(r["ml_dps_pct"] >= r["hc_dps_pct"] - 5 for r in rows)

    dps_deltas = [r["ml_dps_pct"] - r["hc_dps_pct"] for r in rows]
    dps_deltas_sorted = sorted(dps_deltas)
    n = len(rows)
    median_d = dps_deltas_sorted[n // 2] if n % 2 == 1 else (dps_deltas_sorted[n // 2 - 1] + dps_deltas_sorted[n // 2]) / 2.0

    wins = sum(1 for d in dps_deltas if d > 0.05)
    losses = sum(1 for d in dps_deltas if d < -0.05)
    ties = sum(1 for d in dps_deltas if abs(d) <= 0.05)

    verdict = "PASS" if (dps_pass or ovl_pass) and per_build_dps else "FAIL"

    report = {
        "rows": rows,
        "avg": {
            "ml_dps_pct": round(ml_dps_avg, 1),
            "hc_dps_pct": round(hc_dps_avg, 1),
            "ml_overlap": round(ml_ovl_avg, 1),
            "hc_overlap": round(hc_ovl_avg, 1),
        },
        "robustness": {
            "median_dps_delta": round(median_d, 1),
            "wins": wins,
            "losses": losses,
            "ties": ties,
        },
        "gate": {
            "dps_primary": dps_pass,
            "overlap_secondary": ovl_pass,
            "per_build_dps_floor": per_build_dps,
        },
        "verdict": verdict,
    }

    with _lock:
        _state["eval_report"] = report

    _sync_broadcast({"type": "eval_done", "report": report})
    _emit_log(f"Eval verdict: {verdict} | ML DPS avg={ml_dps_avg:.1f}% HC DPS avg={hc_dps_avg:.1f}%")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    html_path = Path(__file__).parent / "dashboard.html"
    return FileResponse(html_path, media_type="text/html")


@app.get("/api/status")
async def api_status():
    with _lock:
        return {
            "phase": _state["phase"],
            "progress": _state["progress"],
            "train_iters_count": len(_state["train_iters"]),
            "eval_rows_count": len(_state["eval_rows"]),
            "error": _state["error"],
        }


@app.get("/api/start/{cmd}")
async def api_start(cmd: str, workers: int = 4, lambda_blend: float = 0.5, use_ninja: bool = False):
    if cmd not in ("prepare", "train", "eval", "all"):
        return {"error": f"Unknown command: {cmd}"}
    with _lock:
        if _state["phase"] not in ("idle", "done", "error"):
            return {"error": f"Pipeline already running ({_state['phase']})"}
        _state["phase"] = "starting"
        _state["progress"] = 0
        _state["train_iters"] = []
        _state["eval_rows"] = []
        _state["eval_report"] = None
        _state["logs"] = []
        _state["error"] = None

    thread = threading.Thread(
        target=_run_pipeline,
        args=(cmd, workers, lambda_blend, use_ninja),
        daemon=True,
    )
    thread.start()
    return {"status": "started", "cmd": cmd}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    _clients.append(websocket)
    # Send current state snapshot
    with _lock:
        snapshot = {
            "type": "snapshot",
            "phase": _state["phase"],
            "progress": _state["progress"],
            "train_iters": _state["train_iters"][-200:],
            "eval_rows": _state["eval_rows"],
            "eval_report": _state["eval_report"],
            "logs": _state["logs"][-100:],
        }
    await websocket.send_text(json.dumps(snapshot, ensure_ascii=False, default=str))
    try:
        while True:
            await websocket.receive_text()  # keep alive
    except WebSocketDisconnect:
        if websocket in _clients:
            _clients.remove(websocket)


# ---------------------------------------------------------------------------
# Startup: capture event loop
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def _on_startup():
    global _event_loop
    _event_loop = asyncio.get_event_loop()


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = 8765
    print(f"\n  [>>>] ML-v0 Dashboard: http://localhost:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
