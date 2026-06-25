"""Смоук-тест: какие Lua-рантаймы есть в lupa и заведётся ли LuaJIT + lua-utf8.dll."""

from __future__ import annotations

import os

import lupa

POB = os.path.abspath(os.path.join("vendor", "PathOfBuilding-2.65.0"))
RT = os.path.join(POB, "runtime")

print("lupa version:", getattr(lupa, "__version__", "?"))
print("lupa submodules:", [m for m in dir(lupa) if m.startswith(("lua", "luajit"))])

# Пытаемся получить именно LuaJIT 2.1.
LuaRuntime = None
for modname in ("luajit21", "luajit20"):
    mod = getattr(lupa, modname, None)
    if mod is not None:
        LuaRuntime = mod.LuaRuntime
        print("USING:", modname)
        break

if LuaRuntime is None:
    print("!!! LuaJIT runtime недоступен в этом колесе lupa")
    raise SystemExit(2)

lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)  # encoding=None -> bytes, без UTF-8 декода
print("jit.version:", lua.eval(b"jit and jit.version or 'none'"))
print("bit type:", lua.eval(b"type(bit)"))

lua.execute(("package.cpath = [[" + RT + "\\?.dll;]] .. package.cpath").encode())
lua.execute(("package.path  = [[" + RT + "\\lua\\?.lua;]] .. package.path").encode())

print("--- trying require('lua-utf8') ---")
res = lua.eval(b"{pcall(require, 'lua-utf8')}")
print("require lua-utf8 ok:", res[1])
if not res[1]:
    print("error bytes:", bytes(res[2]) if res[2] is not None else None)
