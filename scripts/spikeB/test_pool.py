"""Smoke-тест пула: N воркеров, батч запросов, проверка ok + времени."""

import time

from poebuildgen.pool import WorkerPool

reqs = [{"xml": None, "stats": ["Life", "Str", "Int"]} for _ in range(8)]

t0 = time.time()
with WorkerPool(n_workers=4) as pool:
    boot = time.time() - t0
    t1 = time.time()
    res = pool.map(reqs)
    dt = time.time() - t1

ok = sum(1 for r in res if r.get("ok"))
print(f"workers=4 boot={boot:.1f}s map(8)={dt:.1f}s ok={ok}/8")
print("sample:", res[0])
