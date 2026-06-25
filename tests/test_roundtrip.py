"""Round-trip (детерминизм без GUI) — self-gen вариант критерия Spike A.

Каждая оценка идёт в изолированном процессе (poebuildgen.evaluator), что
соответствует §6 (изоляция состояния) и обходит SEH-краши LuaJIT при нескольких
VM в одном процессе.

Цепочка: fixture-XML -> headless -> export-XML' -> headless' ; статы совпадают.
Плюс прогон через wire-кодек и сверка пересчёта с вшитыми <PlayerStat>.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytest.importorskip("lupa")

from poebuildgen import pobcode  # noqa: E402
from poebuildgen.evaluator import evaluate  # noqa: E402
from poebuildgen.headless import _default_pob_dir  # noqa: E402

_HAS_POB = (_default_pob_dir() / "src" / "HeadlessWrapper.lua").exists()
pytestmark = pytest.mark.skipif(not _HAS_POB, reason="vendor PoB не распакован")

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "witch_fireball.pob.xml"
KEYS = ["TotalDPS", "AverageDamage", "Life", "Mana"]
TOL = 0.001  # детерминизм одного движка — практически точный


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-9)


def test_fixture_exists():
    assert FIXTURE.exists(), "сначала запусти scripts/gen_fixture.py"


def test_headless_determinism_across_instances():
    xml0 = FIXTURE.read_text(encoding="utf-8")
    a = evaluate(xml0, KEYS, want_export=True, name="A")
    b = evaluate(a["export"], KEYS, name="B")
    for k in KEYS:
        assert _rel(a["stats"][k], b["stats"][k]) <= TOL, f"{k}: {a['stats'][k]} vs {b['stats'][k]}"


def test_wire_codec_lossless_and_recompute_stable():
    xml0 = FIXTURE.read_text(encoding="utf-8")
    a = evaluate(xml0, KEYS, want_export=True, name="A")
    xml1 = a["export"]

    code = pobcode.encode(xml1.encode("utf-8"))
    decoded = pobcode.decode(code)
    assert decoded == xml1.encode("utf-8"), "кодек не lossless"

    c = evaluate(decoded.decode("utf-8"), KEYS, name="C")
    for k in KEYS:
        assert _rel(a["stats"][k], c["stats"][k]) <= TOL, f"{k}: {a['stats'][k]} vs {c['stats'][k]}"


def test_embedded_playerstat_matches_recompute():
    xml0 = FIXTURE.read_text(encoding="utf-8")
    root = ET.fromstring(xml0)
    embedded = {ps.attrib["stat"]: float(ps.attrib["value"]) for ps in root.iter("PlayerStat")}
    assert "TotalDPS" in embedded, "в фикстуре нет вшитого TotalDPS"

    a = evaluate(xml0, KEYS, name="A")
    for k in ("TotalDPS", "Life"):
        if k in embedded:
            assert _rel(embedded[k], a["stats"][k]) <= TOL, (
                f"{k}: вшито {embedded[k]} vs пересчёт {a['stats'][k]}"
            )
