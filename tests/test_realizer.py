from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("lupa")

from poebuildgen import pobcode
from poebuildgen.headless import _default_pob_dir
from poebuildgen.model import PobBuild
from poebuildgen.pool import WorkerPool
from poebuildgen.realizer import Realizer

_HAS_POB = (_default_pob_dir() / "src" / "HeadlessWrapper.lua").exists()
pytestmark = pytest.mark.skipif(not _HAS_POB, reason="нет vendor PoB")

CODE_FILE = Path(__file__).resolve().parent / "fixtures" / "user_build.txt"


def _load_user_build() -> PobBuild:
    code = CODE_FILE.read_text(encoding="utf-8").strip()
    xml = pobcode.decode(code).decode("utf-8")
    return PobBuild.from_xml(xml)


def test_realizer_tree_only_minimal():
    build = _load_user_build()
    with WorkerPool(n_workers=1) as pool:
        realizer = Realizer(pool=pool)
        # tree_only=True => skip gear phase. Fast execution.
        out = realizer.realize(
            build,
            budget=80,
            gear_start="stripped",
            tree_start="minimal",
            joint_iters=1,
            tree_rounds=2,
            tree_only=True,
        )
        assert isinstance(out, PobBuild)
        # Check that we can export back to XML and code
        xml = out.to_xml()
        assert "<PathOfBuilding>" in xml
        assert out.to_code()


def test_realizer_tree_only_both():
    build = _load_user_build()
    with WorkerPool(n_workers=1) as pool:
        realizer = Realizer(pool=pool)
        # tree_start="both" => tries both ml (which falls back to minimal if missing) and minimal,
        # then returns the best of them.
        out = realizer.realize(
            build,
            budget=80,
            gear_start="expert",
            tree_start="both",
            joint_iters=1,
            tree_rounds=1,
            tree_only=True,
        )
        assert isinstance(out, PobBuild)
        assert "<PathOfBuilding>" in out.to_xml()


def test_realizer_joint_with_gear():
    build = _load_user_build()
    with WorkerPool(n_workers=1) as pool:
        realizer = Realizer(pool=pool)
        # Runs full fixpoint loop including CP-SAT solver and gear swaps.
        # Run with very short rounds and iters to verify end-to-end integration quickly.
        out = realizer.realize(
            build,
            budget=80,
            gear_start="expert",
            tree_start="minimal",
            joint_iters=1,
            tree_rounds=1,
            life_frac=0.5,
            tree_only=False,
        )
        assert isinstance(out, PobBuild)
        assert "<PathOfBuilding>" in out.to_xml()
