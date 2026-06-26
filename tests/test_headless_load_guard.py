"""Тест для проверки защиты от бесшумного падения загрузки билда (silent crash / false positive).

Проверяет, что при передаче поврежденного или некорректного XML-кода воркер PoB 
выбрасывает ошибку, а не возвращает статы предыдущего успешно загруженного билда.
"""

from __future__ import annotations

import pytest
from poebuildgen.evaluator import evaluate, PobEvalError
from poebuildgen.headless import _default_pob_dir

_HAS_POB = (_default_pob_dir() / "src" / "HeadlessWrapper.lua").exists()
pytestmark = pytest.mark.skipif(not _HAS_POB, reason="vendor PoB не распакован")


def test_headless_load_guard_malformed_xml():
    # Передача совершенно некорректного XML должна приводить к ошибке воркера
    with pytest.raises(PobEvalError) as exc_info:
        evaluate(xml="not a valid xml code", stats=["Life"])
    assert "PobHeadlessError" in str(exc_info.value) or "Ошибка загрузки билда" in str(exc_info.value)


def test_headless_load_guard_missing_sections():
    # Передача XML, который является валидным XML, но не содержит необходимых тегов PoB,
    # должна приводить к ошибке инициализации или парсинга билда.
    bad_xml = "<PathOfBuilding><Build level=\"1\"/></PathOfBuilding>"
    with pytest.raises(PobEvalError) as exc_info:
        evaluate(xml=bad_xml, stats=["Life"])
    assert "PobHeadlessError" in str(exc_info.value) or "Ошибка загрузки билда" in str(exc_info.value)


def test_headless_load_guard_fingerprint():
    # Проверяет, что при передаче фингерпринта, совпадающего с результатом расчета,
    # выбрасывается ошибка (предотвращая false-positive при тихом сбое загрузки).
    from pathlib import Path
    from poebuildgen import pobcode

    code_file = Path(__file__).resolve().parent / "fixtures" / "user_build.txt"
    code = code_file.read_text(encoding="utf-8").strip()
    xml = pobcode.decode(code).decode("utf-8")

    res = evaluate(xml=xml, stats=["CombinedDPS", "TotalDPS", "Life"])
    stats = res["stats"]
    dps = stats.get("CombinedDPS") or stats.get("TotalDPS") or 0.0
    life = stats.get("Life") or 0.0

    assert dps > 0
    assert life > 0

    fingerprint = {
        "CombinedDPS": dps,
        "TotalDPS": dps,
        "Life": life
    }

    with pytest.raises(PobEvalError) as exc_info:
        evaluate(xml=xml, stats=["Life"], fingerprint=fingerprint)
    assert "False positive detected" in str(exc_info.value)
