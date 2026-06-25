"""Make-or-break: реально ли загрузить PoB HeadlessWrapper в LuaJIT из lupa.

Подменяем нативные C-модули pure-Lua шимами, грузим Launch.lua, проверяем, что
поднялся build-режим. Без сети, без графики.
"""

from __future__ import annotations

import os

import lupa

POB = os.path.abspath(os.path.join("vendor", "PathOfBuilding-2.65.0"))
SRC = os.path.join(POB, "src")
RT = os.path.join(POB, "runtime")
SRC_LUA = SRC.replace("\\", "/")
RT_LUA = RT.replace("\\", "/")

LuaRuntime = lupa.luajit21.LuaRuntime  # type: ignore[attr-defined]
lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)

# --- Прелюдия: пути + шимы нативных модулей ---
prelude = f"""
package.path = "{SRC_LUA}/?.lua;{SRC_LUA}/?/init.lua;{RT_LUA}/lua/?.lua;{RT_LUA}/lua/?/init.lua;" .. package.path

-- lua-utf8 -> фолбэк на string (ASCII-достаточно для headless)
local u = setmetatable({{}}, {{__index = string}})
u.next = function(s, pos, dir)
    dir = dir or 1
    pos = (pos or 0) + dir
    if pos < 1 then return nil end
    if pos > #s + 1 then return nil end
    return pos
end
u.offset = function(s, n) return n end
package.loaded["lua-utf8"] = u

-- lzip / lcurl.safe -> заглушки (нужны только для апдейтера/сети)
package.loaded["lzip"] = {{}}
package.loaded["lcurl.safe"] = false

-- Доп. host-функции SimpleGraphic, не покрытые старым HeadlessWrapper
function GetVirtualScreenSize() return 1920, 1080 end
function GetVirtualScreenRect() return 0, 0, 1920, 1080 end

-- standalone-Lua задаёт глобал arg (аргументы CLI); lupa — нет
if arg == nil then arg = {{}} end
"""
lua.execute(prelude.encode())

# --- Текст HeadlessWrapper без shebang и без блокирующего io.read ---
with open(os.path.join(SRC, "HeadlessWrapper.lua"), "r", encoding="utf-8") as f:
    wrapper = f.read()
# Срезаем первую строку, если это shebang-маркер (#@).
if wrapper.startswith("#"):
    wrapper = wrapper.split("\n", 1)[1]
wrapper = wrapper.replace('io.read("*l")', "-- io.read removed")

os.chdir(SRC)
ok, err = True, None
res = lua.eval(b"function(txt) return pcall(loadstring(txt)) end")(wrapper.encode())
# res: (pcall_ok, ...) — но loadstring может вернуть nil при синтакс-ошибке
try:
    if isinstance(res, tuple):
        ok = bool(res[0])
        err = res[1] if len(res) > 1 else None
    else:
        ok = bool(res)
except Exception as exc:  # noqa: BLE001
    ok, err = False, repr(exc)

print("pcall ok:", ok)
if err is not None:
    try:
        print("err:", bytes(err).decode("cp1251", "replace"))
    except Exception:
        print("err(raw):", err)

print("build global present:", lua.eval(b"build ~= nil"))
promptmsg = lua.eval(b"mainObject and mainObject.promptMsg or nil")
if promptmsg is not None:
    print("promptMsg:", bytes(promptmsg).decode("cp1251", "replace"))
else:
    print("promptMsg: <none>  (startup OK)")
