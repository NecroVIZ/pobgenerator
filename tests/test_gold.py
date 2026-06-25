"""Золотой тест Spike A: headless-пересчёт == GUI-цифры реального билда (±1%).

Реальный билд-код (Marauder/Chieftain Blade Vortex с деревом, предметами и
таймлесс-джевелом) лежит в fixtures/user_build.txt. Сверяем headless-пересчёт с
вшитыми <PlayerStat> (их посчитал GUI PoB при экспорте).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytest.importorskip("lupa")

from poebuildgen import pobcode  # noqa: E402
from poebuildgen.evaluator import evaluate  # noqa: E402
from poebuildgen.headless import _default_pob_dir  # noqa: E402

CODE = Path(__file__).resolve().parent / "fixtures" / "user_build.txt"
_HAS_POB = (_default_pob_dir() / "src" / "HeadlessWrapper.lua").exists()
pytestmark = pytest.mark.skipif(
    not (_HAS_POB and CODE.exists()), reason="нет vendor PoB или фикстуры билда"
)

TOL = 0.01


def _embedded(xml: str) -> dict[str, float]:
    root = ET.fromstring(xml)
    out = {}
    for ps in root.iter("PlayerStat"):
        try:
            out[ps.attrib["stat"]] = float(ps.attrib["value"])
        except (KeyError, ValueError):
            pass
    return out


def test_headless_matches_gui_embedded_stats():
    xml = pobcode.decode(CODE.read_text(encoding="utf-8").strip()).decode("utf-8")
    embedded = _embedded(xml)
    assert len(embedded) >= 50, "в билде неожиданно мало PlayerStat"

    res = evaluate(xml, list(embedded.keys()), name="gold")
    rec = res["stats"]

    fails = []
    total = 0
    for k, gui in embedded.items():
        hl = rec.get(k)
        if hl is None:
            continue
        total += 1
        rel = abs(gui - hl) / max(abs(gui), abs(hl), 1e-9)
        if rel > TOL:
            fails.append((k, gui, hl, round(rel * 100, 3)))

    assert total >= 50
    assert not fails, f"расхождения headless vs GUI: {fails[:12]}"


def test_key_stats_present_and_sane():
    xml = pobcode.decode(CODE.read_text(encoding="utf-8").strip()).decode("utf-8")
    res = evaluate(xml, ["TotalDPS", "Life", "TotalEHP"], name="gold")
    s = res["stats"]
    assert s["TotalDPS"] and s["TotalDPS"] > 1_000_000
    assert s["Life"] and s["Life"] > 4000
    assert s["TotalEHP"] and s["TotalEHP"] > 100_000
