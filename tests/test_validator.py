"""Тесты контракт-валидатора (PoB-оракул распознавания)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lupa")

from poebuildgen import pobcode  # noqa: E402
from poebuildgen.headless import _default_pob_dir  # noqa: E402
from poebuildgen.validator import validate  # noqa: E402

_HAS_POB = (_default_pob_dir() / "src" / "HeadlessWrapper.lua").exists()
pytestmark = pytest.mark.skipif(not _HAS_POB, reason="нет vendor PoB")

CODE = Path(__file__).resolve().parent / "fixtures" / "user_build.txt"


def _real_xml() -> str:
    return pobcode.decode(CODE.read_text(encoding="utf-8").strip()).decode("utf-8")


def test_empty_build_is_valid_without_skill():
    report = validate(None, require_skill=False)
    assert report.loaded
    assert report.ok, report.summary()


@pytest.mark.skipif(not CODE.exists(), reason="нет фикстуры реального билда")
def test_real_build_passes_contract():
    report = validate(_real_xml())
    assert report.ok, report.summary()
    assert report.main_skill, "активный скилл реального билда не распознан"
    assert report.masteries_unselected == 0


@pytest.mark.skipif(not CODE.exists(), reason="нет фикстуры реального билда")
def test_broken_gem_is_flagged():
    # Берём валидный билд и подменяем поля распознавания гема на мусор -> socket group
    # создастся, но гем не разрешится (нет gemData/grantedEffect).
    xml = _real_xml()
    broken = (
        xml.replace('skillId="BladeVortex"', 'skillId="ZzzNotARealSkill"')
        .replace("Metadata/Items/Gems/SkillGemBladeVortex", "Metadata/Items/Gems/Zzz")
        .replace('nameSpec="Blade Vortex"', 'nameSpec="Zzz Not Real"')
    )
    assert broken != xml, "подмена гема не сработала — изменился формат фикстуры"

    report = validate(broken, require_skill=False)
    assert report.loaded
    assert not report.ok, report.summary()
    assert "gem" in {i.kind for i in report.errors}, report.summary()
