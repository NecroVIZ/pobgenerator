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
