"""Тест data-gate (D4): покрытие данных вендоренного PoB под механики патча."""

from __future__ import annotations

import pytest

pytest.importorskip("lupa")

from poebuildgen.datagate import ALL_TIMELESS, EXPECTED_TREE_VERSION, audit  # noqa: E402
from poebuildgen.headless import _default_pob_dir  # noqa: E402

_HAS_POB = (_default_pob_dir() / "src" / "HeadlessWrapper.lua").exists()
pytestmark = pytest.mark.skipif(not _HAS_POB, reason="vendor PoB не распакован")


def test_data_gate_passes():
    rep = audit()
    assert rep.ok, rep.summary()


def test_data_gate_specifics():
    rep = audit()
    assert rep.pob_version and rep.pob_version.startswith("2.")
    assert rep.tree_version == EXPECTED_TREE_VERSION
    assert rep.gem_count > 500
    # все 6 типов таймлесс-джевелов грузятся (в т.ч. split-файлы GloriousVanity)
    assert all(rep.timeless.get(name) for name in ALL_TIMELESS), rep.timeless
    # все опорные гемы распознаны
    assert all(rep.staple_gems.values()), rep.staple_gems
    # все образцовые моды парсятся без остатка
    assert all(rep.mods.values()), rep.mods


def test_newfilesearch_via_datagate():
    # NewFileSearch покрыт через subprocess (без in-process PobHeadless, см. B1):
    # перечисление split-частей GloriousVanity и nil на отсутствующем файле.
    rep = audit()
    assert rep.nfs.get("gv_parts") == 5, rep.nfs
    assert rep.nfs.get("missing_nil") is True, rep.nfs
