"""Проба: сколько headless-инстансов переживает один процесс."""

from __future__ import annotations

from poebuildgen.headless import PobHeadless

for i in range(1, 4):
    print(f"creating instance #{i} ...", flush=True)
    pob = PobHeadless()
    pob.new_build()
    print(f"  #{i} ok, Life=", pob.stat("Life"), flush=True)
print("DONE")
