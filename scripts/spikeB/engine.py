"""Движки спайка B.

PoolEngine — батч-оценки DPS через пул тёплых воркеров (throughput ×N по ядрам).
MetaEngine — один in-process PobHeadless: нужен ТОЛЬКО для доступа к данным PoB
(data.itemMods через Lua-eval в ModPool) и финальной валидации шмота. DPS им не
считаем (это делает пул). Один in-process инстанс безопасен (краши были при >1).
"""

from __future__ import annotations

from poebuildgen.headless import PobHeadless
from poebuildgen.pool import WorkerPool, _dps_from

DPS_CANDIDATES = ("CombinedDPS", "TotalDPS", "FullDPS")


class PoolEngine:
    def __init__(self, n_workers: int | None = None, prefer: str = "CombinedDPS") -> None:
        self.pool = WorkerPool(n_workers)
        self.prefer = prefer
        self._evals = 0

    def dps_batch(self, xmls: list[str]) -> list[float]:
        reqs = [{"xml": x, "stats": list(DPS_CANDIDATES)} for x in xmls]
        res = self.pool.map(reqs)
        self._evals += len(res)
        return [_dps_from(r, self.prefer, DPS_CANDIDATES) for r in res]

    def dps(self, xml: str) -> float:
        return self.dps_batch([xml])[0]

    def stats_batch(self, xmls: list[str], keys) -> list[dict]:
        reqs = [{"xml": x, "stats": list(keys)} for x in xmls]
        res = self.pool.map(reqs)
        self._evals += len(res)
        return [r.get("stats", {}) if r.get("ok") else {} for r in res]

    def stats(self, xml: str, keys) -> dict:
        return self.stats_batch([xml], keys)[0]

    @property
    def evals(self) -> int:
        return self._evals

    def close(self) -> None:
        self.pool.close()


class MetaEngine:
    """In-process PoB для данных модов и валидации (не для DPS)."""

    def __init__(self) -> None:
        self.pob = PobHeadless()
        self.pob.new_build()

    def validate(self, xml: str) -> dict:
        self.pob.load_build_xml(xml)
        return self.pob.validate()

    def stat(self, name: str):
        return self.pob.stat(name)
