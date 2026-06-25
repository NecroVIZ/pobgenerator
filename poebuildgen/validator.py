"""Контракт-валидатор билдов: PoB как оракул распознавания.

Идея (DESIGN-v2): прежде чем доверять статам сгенерированного билда, надо
убедиться, что PoB РАСПОЗНАЛ всё, что мы в него положили. Молча «съеденный»
мод, ненайденный гем или невыбранный эффект мастери дают неверный фитнес и
отравляют поиск меты.

Уровни:
  ERROR   — билд посчитан неверно/неполно (нельзя доверять фитнесу):
            не загрузился, нет активного скилла, гем не найден/не поддержан,
            аллоцированная мастери без выбранного эффекта.
  WARNING — потенциальная тихая потеря (надо посмотреть глазами):
            неразобранный остаток мода предмета (modLine.extra).

Использование:
    from poebuildgen.validator import validate
    report = validate(xml)            # отдельный изолированный процесс PoB
    assert report.ok, report.summary()
"""

from __future__ import annotations

from dataclasses import dataclass, field

from poebuildgen.evaluator import evaluate


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    kind: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.kind}: {self.message}"


@dataclass
class BuildValidation:
    loaded: bool
    main_skill: str | None
    masteries_allocated: int
    masteries_unselected: int
    issues: list[Issue] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return self.loaded and not self.errors

    @property
    def has_unrecognized_mods(self) -> bool:
        """PoB распознал мод частично (modLine.extra) — DPS может быть занижен."""
        return any(i.code == "item_mod" for i in self.warnings)

    def caveats(self) -> list[str]:
        """Предупреждения для BuildOutput (production): не silent-DPS-loss."""
        out: list[str] = []
        n = sum(1 for i in self.warnings if i.code == "item_mod")
        if n:
            out.append(
                f"PoB частично не распознал {n} мод(ов) (modLine.extra); "
                "цифры могут быть занижены"
            )
        return out

    def summary(self) -> str:
        head = (
            f"loaded={self.loaded} skill={self.main_skill!r} "
            f"errors={len(self.errors)} warnings={len(self.warnings)}"
        )
        if not self.issues:
            return head
        return head + "\n" + "\n".join("  " + str(i) for i in self.issues)


def _report_from_raw(raw: dict, *, require_skill: bool) -> BuildValidation:
    loaded = bool(raw.get("loaded"))
    main_skill = raw.get("main_skill")
    alloc = int(raw.get("masteries_allocated") or 0)
    unsel = int(raw.get("masteries_unselected") or 0)
    issues: list[Issue] = []

    if not loaded:
        issues.append(Issue("error", "not_loaded", "PoB не загрузил билд"))
        return BuildValidation(loaded, main_skill, alloc, unsel, issues, raw)

    if require_skill and not main_skill:
        issues.append(
            Issue("error", "no_main_skill", "активный скилл не распознан (DPS не считается)")
        )

    for ge in raw.get("gem_errors", []):
        issues.append(
            Issue(
                "error",
                "gem",
                f"{ge.get('group') or '?'}: {ge.get('gem')} — {ge.get('err')}",
            )
        )

    if unsel > 0:
        issues.append(
            Issue(
                "error",
                "mastery",
                f"{unsel}/{alloc} мастери аллоцированы без выбранного эффекта",
            )
        )

    for ip in raw.get("item_problems", []):
        line = (ip.get("line") or "").strip()
        extra = (ip.get("extra") or "").strip()
        issues.append(
            Issue(
                "warning",
                "item_mod",
                f"{ip.get('slot')} ({ip.get('item')}): нераспознан «{extra}» в «{line}»",
            )
        )

    return BuildValidation(loaded, main_skill, alloc, unsel, issues, raw)


def validate(
    build,
    *,
    require_skill: bool = True,
    timeout: float = 180,
) -> BuildValidation:
    """Прогнать билд через изолированный PoB и вернуть отчёт о распознавании.

    build: XML-строка, None (пустой билд) или объект с методом .to_xml() (PobBuild).
    require_skill=False — для пустого/каркасного билда без активного скилла.
    """
    xml = build.to_xml() if hasattr(build, "to_xml") else build
    res = evaluate(xml=xml, want_validate=True, name="validate", timeout=timeout)
    raw = res.get("validation") or {}
    return _report_from_raw(raw, require_skill=require_skill)
