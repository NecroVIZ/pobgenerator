"""Драйвер оценки билдов через изолированный headless-воркер (процесс на оценку)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

_REPO = Path(__file__).resolve().parent.parent


class PobEvalError(RuntimeError):
    pass


def evaluate(
    xml: str | None = None,
    stats: Sequence[str] | Iterable[str] = (),
    *,
    want_export: bool = False,
    want_validate: bool = False,
    want_audit: bool = False,
    name: str = "eval",
    timeout: float = 180,
) -> dict:
    """Посчитать билд в отдельном процессе PoB и вернуть статы.

    xml=None -> пустой дефолтный билд.
    Возвращает {"version","stats",["export"],["validation"],["audit"]}.
    """
    req = {
        "stats": list(stats),
        "want_export": want_export,
        "want_validate": want_validate,
        "want_audit": want_audit,
        "name": name,
    }
    if xml is not None:
        req["xml"] = xml

    with tempfile.TemporaryDirectory() as td:
        inp = Path(td) / "in.json"
        outp = Path(td) / "out.json"
        inp.write_text(json.dumps(req), encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "poebuildgen.worker", str(inp), str(outp)],
                cwd=str(_REPO),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise PobEvalError(f"воркер превысил таймаут {timeout}s") from exc

        if not outp.exists():
            tail = (proc.stderr or "")[-800:]
            raise PobEvalError(
                f"воркер не создал результат (rc={proc.returncode}). stderr:\n{tail}"
            )
        res = json.loads(outp.read_text(encoding="utf-8"))

    if not res.get("ok"):
        raise PobEvalError(res.get("error", "неизвестная ошибка воркера"))
    return res
