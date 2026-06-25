"""Регрессия Phase 0: headless-PoB поднимается и считает статы — ЧЕРЕЗ subprocess.

ВАЖНО (B1): in-process PobHeadless() в pytest-сеансе несовместим с subprocess-тестами
(validator/datagate/gold/...). Обе ветки грузят одну LuaJIT-DLL, и комбинированный
teardown падает SEH `0xe24c4a02` -> pytest exit 1, хотя assertion'ы зелёные. Поэтому
ВСЕ тесты гоняют PoB только в изолированном процессе (evaluator). In-process путь
допустим лишь в одноразовых scripts/.
"""

from __future__ import annotations

import pytest

pytest.importorskip("lupa")

from poebuildgen.evaluator import evaluate  # noqa: E402
from poebuildgen.headless import _default_pob_dir  # noqa: E402

_HAS_POB = (_default_pob_dir() / "src" / "HeadlessWrapper.lua").exists()
pytestmark = pytest.mark.skipif(not _HAS_POB, reason="vendor PoB не распакован")


def test_pob_version():
    res = evaluate(xml=None, name="ver")
    v = res.get("version")
    assert v and v.split(".")[0] == "2"


def test_empty_build_has_sane_defaults():
    res = evaluate(xml=None, stats=["Life", "Mana", "TotalEHP"], name="defaults")
    s = res["stats"]
    assert s["Life"] and s["Life"] > 0
    assert s["Mana"] and s["Mana"] > 0
    assert s["TotalEHP"] and s["TotalEHP"] > 0
