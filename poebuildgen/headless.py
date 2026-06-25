"""Headless-обёртка Path of Building поверх LuaJIT из lupa.

Запускает движок PoB без графики/сети во встроенном LuaJIT (lupa.luajit21).
Нативные C-модули PoB (lua-utf8, lzip, lcurl) НЕ используются — вместо них
pure-Lua/Python-шимы, т.к. они скомпилированы под отдельный lua51.dll и
несовместимы со встроенным в lupa LuaJIT (две разные VM).

Важно: распаковка (Inflate/Deflate) реализована через Python zlib и подменяется
в глобалы PoB — иначе не читаются сжатые data-файлы (напр. TimelessJewelData),
из-за чего молча ломается загрузка дерева.

Рантайм работает в режиме encoding=None: все Lua-строки ходят как bytes (нужно
для бинарных данных). На границах Python<->Lua строки кодируются в utf-8.
"""

from __future__ import annotations

import glob as _glob_mod
import os
import zlib
from pathlib import Path
from typing import Iterable

import lupa


def _default_pob_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "vendor" / "PathOfBuilding-2.65.0"


_PRELUDE = r"""
package.path = "{SRC}/?.lua;{SRC}/?/init.lua;{RT}/lua/?.lua;{RT}/lua/?/init.lua;" .. package.path

-- lua-utf8 -> фолбэк на string (ASCII достаточно для разбора билдов/статов)
local u = setmetatable({}, {__index = string})
u.next = function(s, pos, dir)
    dir = dir or 1
    pos = (pos or 0) + dir
    if pos < 1 or pos > #s + 1 then return nil end
    return pos
end
u.offset = function(s, n) return n end
package.loaded["lua-utf8"] = u

-- Сетевой/архивный модуль апдейтера не нужен headless
package.loaded["lzip"] = {}
package.loaded["lcurl.safe"] = false

-- Host-функции SimpleGraphic, не покрытые штатным HeadlessWrapper новой версии PoB
function GetVirtualScreenSize() return 1920, 1080 end
function GetVirtualScreenRect() return 0, 0, 1920, 1080 end

-- standalone-Lua задаёт глобал arg; lupa — нет
if arg == nil then arg = {} end
"""


def _inflate(data) -> bytes:
    if data is None:
        return b""
    raw = bytes(data)
    if not raw:
        return b""
    for wbits in (15, -15, 47):  # zlib, raw deflate, gzip/auto
        try:
            return zlib.decompress(raw, wbits)
        except zlib.error:
            continue
    return b""


def _deflate(data) -> bytes:
    raw = bytes(data) if data is not None else b""
    return zlib.compress(raw, 9)


def _file_search(pattern, find_dirs=False) -> bytes:
    """Бэкенд для host-функции NewFileSearch (в HeadlessWrapper она застаблена).

    Нужна для split-файлов таймлесс-джевелов (GloriousVanity.zip.part0..N): PoB
    перечисляет их через NewFileSearch(...".zip.part*") и склеивает. Возвращаем
    строки "basename\\tmtime", разделённые \\n (Lua сам распарсит в handle-таблицу).
    """
    pat = pattern.decode("utf-8") if isinstance(pattern, (bytes, bytearray)) else str(pattern)
    want_dir = bool(find_dirs)
    lines: list[str] = []
    for p in _glob_mod.glob(pat):
        is_dir = os.path.isdir(p)
        if want_dir != is_dir:
            continue
        base = os.path.basename(p.rstrip("/\\")) or p
        try:
            mtime = int(os.path.getmtime(p))
        except OSError:
            mtime = 0
        lines.append(f"{base}\t{mtime}")
    return "\n".join(lines).encode("utf-8")


# Реализация NewFileSearch на Lua: глоб делает Python (_file_search), а handle с
# методами :GetFileName()/:GetFileModifiedTime()/:NextFile() собираем Lua-таблицей,
# чтобы корректно работал self при вызове через двоеточие.
_NFS_LUA = r"""
function NewFileSearch(pattern, findDirectories)
  local joined = __pob_file_search(pattern, findDirectories and true or false)
  if not joined or joined == "" then return nil end
  local names, mtimes = {}, {}
  for line in (joined .. "\n"):gmatch("([^\n]+)\n") do
    local nm, mt = line:match("^(.-)\t(%d+)$")
    if nm then
      names[#names + 1] = nm
      mtimes[#mtimes + 1] = tonumber(mt)
    else
      names[#names + 1] = line
      mtimes[#mtimes + 1] = 0
    end
  end
  if #names == 0 then return nil end
  local idx = 1
  local h = {}
  function h:GetFileName() return names[idx] end
  function h:GetFileModifiedTime() return mtimes[idx] end
  function h:GetFileSize() return 0 end
  function h:NextFile()
    idx = idx + 1
    return idx <= #names
  end
  return h
end
"""


# Валидатор-«оракул»: после загрузки билда спрашиваем у самого PoB, что он РАСПОЗНАЛ.
# Возвращает Lua-таблицу (маршалится в Python через _lua_to_py).
#   main_skill          — имя активного скилла (или nil, если не определился)
#   gem_errors[]        — {group, gem, err}: гем не найден/не поддержан (gemInstance.errMsg)
#   item_problems[]     — {slot, item, line, extra}: неразобранный остаток мода (modLine.extra)
#   masteries_allocated / masteries_unselected — мастери без выбранного эффекта = поломка
_VALIDATE_LUA = r"""
function()
  local b = build
  local r = { gem_errors = {}, item_problems = {} }
  if not b then r.loaded = false return r end
  r.loaded = true

  -- активный скилл распознан?
  local okms, name = pcall(function()
    local env = b.calcsTab and b.calcsTab.mainEnv
    local ms = env and env.player and env.player.mainSkill
    if ms and ms.activeEffect and ms.activeEffect.grantedEffect then
      return ms.activeEffect.grantedEffect.name
    end
    return nil
  end)
  r.main_skill = okms and name or nil

  -- гемы: nameSpec не найден / "is not supported yet"
  if b.skillsTab and b.skillsTab.socketGroupList then
    for _, sg in ipairs(b.skillsTab.socketGroupList) do
      local label = sg.label or ""
      if sg.gemList then
        for _, gem in ipairs(sg.gemList) do
          local named = gem.nameSpec and gem.nameSpec ~= ""
          local resolved = gem.gemData ~= nil or gem.grantedEffect ~= nil
          if gem.errMsg and gem.errMsg ~= "" then
            table.insert(r.gem_errors,
              { group = label, gem = gem.nameSpec or "?", err = gem.errMsg })
          elseif named and not resolved then
            -- ProcessSocketGroup не выставляет errMsg, если гем задан по skillId/gemId,
            -- но не нашёлся в data -> ловим по отсутствию gemData/grantedEffect.
            table.insert(r.gem_errors,
              { group = label, gem = gem.nameSpec, err = "gem not recognized" })
          end
        end
      end
    end
  end

  -- предметы: неразобранный остаток мода (PoB рисует его красным)
  local fields = { "implicitModLines", "explicitModLines", "enchantModLines",
                   "scourgeModLines", "crucibleModLines", "classRequirementModLines",
                   "buffModLines" }
  if b.itemsTab and b.itemsTab.slots and b.itemsTab.items then
    for slotName, slot in pairs(b.itemsTab.slots) do
      local id = slot.selItemId
      local item = id and id ~= 0 and b.itemsTab.items[id] or nil
      if item then
        for _, fname in ipairs(fields) do
          local arr = item[fname]
          if arr then
            for _, ml in ipairs(arr) do
              if ml.extra and ml.extra ~= "" then
                table.insert(r.item_problems, { slot = slotName,
                  item = item.name or "?", line = ml.line or "", extra = ml.extra })
              end
            end
          end
        end
      end
    end
  end

  -- мастери: аллоцированы, но без выбранного эффекта
  local alloc, unsel = 0, 0
  if b.spec and b.spec.allocNodes then
    for nid, node in pairs(b.spec.allocNodes) do
      if node.type == "Mastery" then
        alloc = alloc + 1
        if not (b.spec.masterySelections and b.spec.masterySelections[nid]) then
          unsel = unsel + 1
        end
      end
    end
  end
  r.masteries_allocated = alloc
  r.masteries_unselected = unsel
  return r
end
"""


# Data-gate (D4): покрытие данных PoB под механики патча. Проект использует данные
# самого PoB как источник истины (вместо RePoE), поэтому gate проверяет, что
# вендоренные данные присутствуют и согласованы: версия движка/дерева, гемы,
# таймлесс-данные (все 6 типов реально грузятся), парсер модов.
_AUDIT_LUA = r"""
function()
  local r = {}
  r.pob_version = launch and launch.versionNumber or nil
  r.tree_version = latestTreeVersion

  local n = 0
  local byName = {}
  for _, g in pairs(data.gems) do
    n = n + 1
    if g.name then byName[g.name] = g end
  end
  r.gem_count = n

  r.staple_gems = {}
  -- имена саппортов в data.gems идут без суффикса " Support"
  local staples = { "Fireball", "Cyclone", "Blade Vortex", "Spark",
                    "Determination", "Hatred", "Added Fire Damage",
                    "Increased Critical Strikes", "Spell Echo" }
  for _, name in ipairs(staples) do
    local g = byName[name]
    local ge = g and (g.grantedEffect)
    r.staple_gems[name] = g ~= nil and not (ge and ge.unsupported)
  end

  r.timeless = {}
  for tp = 1, 6 do
    local seed = data.timelessJewelSeedMin[tp]
    local nodeID
    for k, v in pairs(data.nodeIDList) do
      if type(v) == "table" and v.index ~= nil then nodeID = k break end
    end
    local ok = pcall(data.readLUT, seed, nodeID, tp)
    r.timeless[data.timelessJewelTypes[tp]] = ok and (data.timelessJewelLUTs[tp] ~= nil)
  end

  r.mods = {}
  local samples = { "+10 to maximum Life", "10% increased maximum Life",
                    "+20% to Fire Resistance", "Adds 5 to 10 Physical Damage to Attacks",
                    "10% increased Attack Speed", "+100 to Armour" }
  for _, line in ipairs(samples) do
    local ok, _ml, extra = pcall(modLib.parseMod, line)
    table.insert(r.mods, { line = line, ok = ok and (extra == nil) })
  end

  -- NewFileSearch: перечисление split-частей GV + nil на отсутствующем файле
  r.nfs = {}
  local sp = GetScriptPath()
  local h = NewFileSearch(sp .. "/Data/TimelessJewelData/GloriousVanity.zip.part*")
  local parts = 0
  while h do
    parts = parts + 1
    if not h:NextFile() then break end
  end
  r.nfs.gv_parts = parts
  r.nfs.missing_nil = (NewFileSearch(sp .. "/Data/__definitely_missing__.zzz") == nil)
  return r
end
"""


class PobHeadlessError(RuntimeError):
    pass


class PobHeadless:
    """Один экземпляр headless-движка PoB (рекомендуется один на процесс).

    ВНИМАНИЕ: конструктор делает os.chdir в src/ движка (PoB грузит data-файлы
    относительными путями). Для изоляции состояния используйте poebuildgen.worker.
    """

    def __init__(self, pob_dir: str | os.PathLike[str] | None = None) -> None:
        self.pob_dir = Path(pob_dir) if pob_dir else _default_pob_dir()
        self.src = self.pob_dir / "src"
        self.runtime = self.pob_dir / "runtime"
        if not (self.src / "HeadlessWrapper.lua").exists():
            raise PobHeadlessError(f"не найден HeadlessWrapper.lua в {self.src}")

        self.lua = lupa.luajit21.LuaRuntime(  # type: ignore[attr-defined]
            unpack_returned_tuples=True, encoding=None
        )
        self._boot()

    # --- внутреннее ---

    def _boot(self) -> None:
        src = self.src.as_posix()
        rt = self.runtime.as_posix()
        prelude = _PRELUDE.replace("{SRC}", src).replace("{RT}", rt)
        self.lua.execute(prelude.encode("utf-8"))

        wrapper = (self.src / "HeadlessWrapper.lua").read_text(encoding="utf-8")
        # срезаем именно shebang-маркер (#@ у PoB, либо #!), а не любой '#'-первый-символ,
        # чтобы случайно не съесть осмысленную первую строку
        if wrapper.startswith("#@") or wrapper.startswith("#!"):
            wrapper = wrapper.split("\n", 1)[1]
        wrapper = wrapper.replace('io.read("*l")', "-- io.read removed (headless)")

        prev_cwd = os.getcwd()
        os.chdir(self.src)
        try:
            loader = self.lua.eval(b"function(txt) return assert(loadstring(txt))() end")
            loader(wrapper.encode("utf-8"))
        except lupa.LuaError as exc:
            os.chdir(prev_cwd)
            raise PobHeadlessError(f"ошибка загрузки HeadlessWrapper: {exc}") from exc

        # Подменяем заглушки распаковки на реальные (Python zlib)
        self.lua.eval(b"function(inf, def) Inflate = inf Deflate = def end")(_inflate, _deflate)

        # GetScriptPath должен указывать на src (иначе scriptPath.."/Data/..." ломается,
        # например при загрузке TimelessJewelData -> тихо рушится дерево)
        self.lua.eval(b"function(p) GetScriptPath = function() return p end end")(
            self.src.as_posix().encode("utf-8")
        )

        # NewFileSearch застаблен в HeadlessWrapper -> без него не грузятся split-файлы
        # таймлесс-джевелов (GloriousVanity.zip.part*). Подменяем на рабочую реализацию.
        self.lua.eval(b"function(fn) __pob_file_search = fn end")(_file_search)
        self.lua.execute(_NFS_LUA.encode("utf-8"))

        # Захватываем анонимные Lua-функции (надёжнее, чем атрибутный доступ при encoding=None)
        self._fn_new = self.lua.eval(b"function() newBuild() end")
        self._fn_loadxml = self.lua.eval(b"function(xml, name) loadBuildFromXML(xml, name or '') end")
        self._fn_frame = self.lua.eval(b"function() runCallback('OnFrame') end")
        self._fn_flag = self.lua.eval(b"function() return build and build.buildFlag or false end")
        self._fn_present = self.lua.eval(b"function() return build ~= nil end")
        self._fn_stat = self.lua.eval(
            b"function(n) local o = build and build.calcsTab and build.calcsTab.mainOutput "
            b"if not o then return nil end return o[n] end"
        )
        self._fn_export = self.lua.eval(b"function() return build:SaveDB('headless') end")
        self._fn_version = self.lua.eval(b"function() return launch and launch.versionNumber or nil end")
        self._fn_prompt = self.lua.eval(b"function() return mainObject and mainObject.promptMsg or nil end")

        if not self._fn_present():
            msg = self._fn_prompt()
            raise PobHeadlessError(f"PoB не инициализировался: {_to_str(msg)}")

    # --- управление билдом ---

    def new_build(self) -> None:
        self._fn_new()
        self.recalc()

    def load_build_xml(self, xml: str | bytes, name: str = "headless") -> None:
        xml_b = xml.encode("utf-8") if isinstance(xml, str) else bytes(xml)
        self._fn_loadxml(xml_b, name.encode("utf-8"))
        self.recalc()

    def recalc(self, max_frames: int = 20) -> None:
        # buildFlag=True => нужен пересчёт; кадр OnFrame обрабатывает его и снимает флаг.
        # Раннее прерывание по первому чистому кадру опасно: флаг может выставиться
        # асинхронно позже (тяжёлое дерево + загрузка GV-LUT). Поэтому ждём конвергенции —
        # два кадра ПОДРЯД без флага, иначе тихая недооценка статов.
        stable = 0
        for _ in range(max_frames):
            self._fn_frame()
            if self._fn_flag():
                stable = 0
            else:
                stable += 1
                if stable >= 2:
                    break

    # --- чтение результатов ---

    def stat(self, name: str, default=None):
        val = self._fn_stat(name.encode("utf-8"))
        return default if val is None else val

    def stats(self, names: Iterable[str]) -> dict[str, float | None]:
        return {n: self.stat(n) for n in names}

    def export_xml(self) -> str:
        xml = self._fn_export()
        if xml is None:
            raise PobHeadlessError("SaveDB вернул nil")
        return _to_str(xml)

    def export_code(self) -> str:
        from poebuildgen import pobcode

        return pobcode.encode(self.export_xml().encode("utf-8"))

    def pob_version(self) -> str | None:
        return _to_str(self._fn_version())

    def prompt_msg(self) -> str | None:
        return _to_str(self._fn_prompt())

    def eval(self, lua_expr: str):
        """Выполнить произвольное Lua-выражение (для диагностики/валидатора)."""
        return self.lua.eval(lua_expr.encode("utf-8") if isinstance(lua_expr, str) else lua_expr)

    def validate(self) -> dict:
        """Спросить у PoB, что он распознал в загруженном билде.

        Возвращает dict: loaded, main_skill, gem_errors[], item_problems[],
        masteries_allocated, masteries_unselected.
        """
        report = _lua_to_py(self.lua.eval(_VALIDATE_LUA.encode("utf-8"))())
        if not isinstance(report, dict):
            return {"loaded": False}
        report.setdefault("gem_errors", [])
        report.setdefault("item_problems", [])
        return report

    def audit_data(self) -> dict:
        """Аудит покрытия данных PoB (data-gate): версия, гемы, таймлесс, моды."""
        report = _lua_to_py(self.lua.eval(_AUDIT_LUA.encode("utf-8"))())
        return report if isinstance(report, dict) else {}


def _to_str(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).decode("utf-8", "replace")
    return str(val)


def _lua_to_py(val):
    """Рекурсивно конвертирует Lua-значение (в т.ч. table-proxy) в Python.

    encoding=None => Lua-строки приходят как bytes; декодируем в str.
    Таблица с ключами 1..n становится списком, иначе — словарём.
    """
    if val is None or isinstance(val, (int, float, bool)):
        return val
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).decode("utf-8", "replace")
    # Lua table-proxy: lupa.lua_type не опознаёт объекты этого рантайма (возвращает
    # None), поэтому детектим по модулю lupa + наличию .items().
    mod = getattr(type(val), "__module__", "") or ""
    if mod.startswith("lupa") and hasattr(val, "items"):
        try:
            items = list(val.items())
        except (TypeError, ValueError):
            return _to_str(val)
        pairs = {_lua_to_py(k): _lua_to_py(v) for k, v in items}
        keys = list(pairs.keys())
        # list только для плотной последовательности 1..n; таблица с дырой ({1,3}) или
        # не-int ключами трактуется как dict (для наших отчётов это безопасно)
        if keys and all(isinstance(k, int) for k in keys) and set(keys) == set(range(1, len(keys) + 1)):
            return [pairs[i] for i in range(1, len(keys) + 1)]
        return pairs
    return _to_str(val)
