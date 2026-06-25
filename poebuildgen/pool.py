"""Пул тёплых headless-воркеров PoB: throughput ×N по ядрам.

Узкое место одиночной оценки — cold-boot PoB (~2-5с). Пул держит N прогретых
процессов (poebuildgen.worker --serve) и раздаёт им запросы динамически (кто
освободился — берёт следующий). Синхронизация по файловым маркерам (.out/.done),
т.к. stdout воркера засоряется логами PoB. stdout/stderr воркеров — в DEVNULL.

Устойчивость: если задача висит дольше таймаута (например, SEH-краш воркера),
воркер пере-спавнится, задача переотправляется (один ретрай).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Sequence

_REPO = Path(__file__).resolve().parent.parent


class PoolError(RuntimeError):
    pass


class WorkerPool:
    def __init__(self, n_workers: int | None = None, *, cwd: str | Path = _REPO,
                 ready_timeout: float = 90, task_timeout: float = 120) -> None:
        if n_workers is None:
            n_workers = max(1, min(6, (os.cpu_count() or 2) - 1))
        self.n = n_workers
        self.cwd = str(cwd)
        self.task_timeout = task_timeout
        self.dir = Path(tempfile.mkdtemp(prefix="pobpool_"))
        self._ctr = 0
        self.procs: list[subprocess.Popen] = []
        self._readies: list[Path] = []
        for i in range(self.n):
            self._spawn(i)
        for i in range(self.n):
            self._await_ready(i, ready_timeout)

    # --- lifecycle ---

    def _spawn(self, i: int) -> None:
        ready = self.dir / f"ready_{i}_{self._ctr}"
        self._ctr += 1
        p = subprocess.Popen(
            [sys.executable, "-m", "poebuildgen.worker", "--serve", str(ready)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=self.cwd, text=True,
        )
        if i < len(self.procs):
            self.procs[i] = p
            self._readies[i] = ready
        else:
            self.procs.append(p)
            self._readies.append(ready)

    def _await_ready(self, i: int, timeout: float) -> None:
        ready = self._readies[i]
        t0 = time.time()
        while time.time() - t0 < timeout:
            if ready.exists():
                return
            if self.procs[i].poll() is not None:
                raise PoolError(f"воркер {i} умер при старте (rc={self.procs[i].returncode})")
            time.sleep(0.05)
        raise PoolError(f"воркер {i} не прогрелся за {timeout}s")

    def close(self) -> None:
        for p in self.procs:
            try:
                if p.stdin:
                    p.stdin.write("QUIT\n")
                    p.stdin.flush()
            except (OSError, ValueError):
                pass
        for p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self) -> "WorkerPool":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- dispatch ---

    def _send(self, i: int, req: dict) -> Path:
        rp = self.dir / f"r_{self._ctr}.json"
        self._ctr += 1
        rp.write_text(json.dumps(req), encoding="utf-8")
        self.procs[i].stdin.write(str(rp) + "\n")  # type: ignore[union-attr]
        self.procs[i].stdin.flush()                 # type: ignore[union-attr]
        return rp

    def _collect(self, rp: Path) -> dict:
        res = json.loads((rp.with_suffix(rp.suffix + ".out")).read_text(encoding="utf-8"))
        for suf in (".out", ".done", ""):
            try:
                (Path(str(rp) + suf) if suf else rp).unlink()
            except OSError:
                pass
        return res

    def map(self, requests: Sequence[dict]) -> list[dict]:
        """Посчитать список запросов параллельно. Порядок результатов сохраняется."""
        n = len(requests)
        results: list[dict | None] = [None] * n
        free = list(range(self.n))
        inflight: dict[int, tuple[int, Path, float, int]] = {}  # worker -> (idx, rp, start, retries)
        next_i = 0

        while next_i < n or inflight:
            while free and next_i < n:
                w = free.pop()
                rp = self._send(w, requests[next_i])
                inflight[w] = (next_i, rp, time.time(), 0)
                next_i += 1

            progressed = False
            for w, (idx, rp, start, retries) in list(inflight.items()):
                done = Path(str(rp) + ".done")
                if done.exists():
                    results[idx] = self._collect(rp)
                    del inflight[w]
                    free.append(w)
                    progressed = True
                elif time.time() - start > self.task_timeout:
                    # воркер завис/умер — пере-спавн и ретрай задачи один раз
                    try:
                        self.procs[w].kill()
                    except OSError:
                        pass
                    self._spawn(w)
                    self._await_ready(w, 90)
                    if retries >= 1:
                        results[idx] = {"ok": False, "error": "task timeout after retry"}
                        del inflight[w]
                        free.append(w)
                    else:
                        rp2 = self._send(w, requests[idx])
                        inflight[w] = (idx, rp2, time.time(), retries + 1)
                    progressed = True
            if not progressed:
                time.sleep(0.01)

        return [r if r is not None else {"ok": False, "error": "no result"} for r in results]


def _dps_from(res: dict, prefer: str, candidates: Sequence[str]) -> float:
    if not res.get("ok"):
        return 0.0
    st = res.get("stats", {})
    order = [prefer] + [c for c in candidates if c != prefer]
    for k in order:
        v = st.get(k)
        if v:
            return float(v)
    return 0.0
