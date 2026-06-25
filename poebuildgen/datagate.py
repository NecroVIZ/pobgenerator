"""Data-gate (D4): покрытие данных PoB под механики патча.

Проект использует данные самого Path of Building как источник истины (а не RePoE),
поэтому gate проверяет, что вендоренные данные присутствуют и согласованы. Это
ловит регрессии при бампе версии PoB: пропавшие/переименованные гемы, нерабочую
загрузку таймлесс-данных, сломанный парсер модов, неожиданную версию дерева.

Использование (в CI / перед прогоном генератора):
    from poebuildgen.datagate import audit
    rep = audit()
    assert rep.ok, rep.summary()
"""

from __future__ import annotations

from dataclasses import dataclass, field

from poebuildgen.evaluator import evaluate

# Ожидаемые опорные значения вендоренной поставки PoB (vendor 2.65.0 / дерево 3.28).
EXPECTED_TREE_VERSION = "3_28"
EXPECTED_POB_MAJOR = "2"
MIN_GEM_COUNT = 200
ALL_TIMELESS = (
    "Glorious Vanity",
    "Lethal Pride",
    "Brutal Restraint",
    "Militant Faith",
    "Elegant Hubris",
    "Heroic Tragedy",
)


@dataclass
class DataGateReport:
    pob_version: str | None
    tree_version: str | None
    gem_count: int
    staple_gems: dict[str, bool]
    timeless: dict[str, bool]
    mods: dict[str, bool]
    nfs: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        head = (
            f"pob={self.pob_version} tree={self.tree_version} gems={self.gem_count} "
            f"timeless={sum(self.timeless.values())}/{len(self.timeless)} "
            f"mods={sum(self.mods.values())}/{len(self.mods)} ok={self.ok}"
        )
        if self.failures:
            head += "\n" + "\n".join("  FAIL: " + f for f in self.failures)
        return head


def _evaluate_failures(raw: dict) -> list[str]:
    fails: list[str] = []

    pob = raw.get("pob_version")
    if not pob:
        fails.append("версия PoB не определена")
    elif str(pob).split(".")[0] != EXPECTED_POB_MAJOR:
        fails.append(f"мажорная версия PoB {pob}, ожидалась {EXPECTED_POB_MAJOR}.x")

    tree = raw.get("tree_version")
    if tree != EXPECTED_TREE_VERSION:
        fails.append(f"версия дерева {tree}, ожидалась {EXPECTED_TREE_VERSION}")

    gems = int(raw.get("gem_count") or 0)
    if gems < MIN_GEM_COUNT:
        fails.append(f"гемов {gems} < ожидаемого минимума {MIN_GEM_COUNT}")

    staples = raw.get("staple_gems") or {}
    for name, present in staples.items():
        if not present:
            fails.append(f"опорный гем не распознан: {name}")

    timeless = raw.get("timeless") or {}
    for name in ALL_TIMELESS:
        if not timeless.get(name):
            fails.append(f"таймлесс-данные не загрузились: {name}")

    mods = {m.get("line"): bool(m.get("ok")) for m in (raw.get("mods") or [])}
    for line, ok in mods.items():
        if not ok:
            fails.append(f"мод не распарсился без остатка: {line!r}")

    nfs = raw.get("nfs") or {}
    if int(nfs.get("gv_parts") or 0) < 1:
        fails.append("NewFileSearch не нашёл split-части GloriousVanity")
    if not nfs.get("missing_nil"):
        fails.append("NewFileSearch не вернул nil на отсутствующем файле")

    return fails


def audit(*, timeout: float = 180) -> DataGateReport:
    """Прогнать аудит данных PoB в изолированном процессе и вернуть отчёт."""
    res = evaluate(xml=None, want_audit=True, name="datagate", timeout=timeout)
    raw = res.get("audit") or {}
    mods = {m.get("line"): bool(m.get("ok")) for m in (raw.get("mods") or [])}
    return DataGateReport(
        pob_version=raw.get("pob_version"),
        tree_version=raw.get("tree_version"),
        gem_count=int(raw.get("gem_count") or 0),
        staple_gems=raw.get("staple_gems") or {},
        timeless=raw.get("timeless") or {},
        mods=mods,
        nfs=raw.get("nfs") or {},
        failures=_evaluate_failures(raw),
        raw=raw,
    )
