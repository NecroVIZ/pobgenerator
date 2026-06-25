"""Корпус-фолдер: фетчит билды с ninja /character API, достаёт PoB-code, декодит, сохраняет.

Источник имён {account, name}: кураторский список (путь C из ML-DIALOGUE).
Endpoint: GET /poe1/api/builds/1/character?account=X&name=Y&overview=LEAGUE&type=exp
Возвращает JSON с полем pathOfBuildingExport = base64+zlib PoB-code.

Уважительный скрейп: задержка между запросами, retry на 429, dedup, resume.
Ничего не удаляет; корпус складывает в corpus/{account}__{name}.pob.xml (+ .meta.json).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from poebuildgen import pobcode

NINJA = "https://poe.ninja/poe1/api/builds/1/character"
UA = "poebuildgen-corpus/0.1 (research; respectful delay)"
CORPUS_DIR = Path(__file__).resolve().parents[1] / "corpus"
DELAY_S = 1.5          # ~40 запросов/мин — консервативно
TIMEOUT_S = 25
RETRY_429_S = 30       # ninja отдаёт Retry-After при rate-limit


@dataclass
class FetchResult:
    ok: bool
    pob_xml: str | None = None
    meta: dict | None = None
    error: str | None = None
    cached: bool = False


def fetch_character(account: str, name: str, league: str = "mirage",
                    type_: str = "exp") -> FetchResult:
    params = {"account": account, "name": name, "overview": league, "type": type_}
    try:
        r = requests.get(NINJA, params=params,
                         headers={"User-Agent": UA, "Accept": "application/json"},
                         timeout=TIMEOUT_S)
    except requests.RequestException as e:
        return FetchResult(ok=False, error=f"network: {e}")

    if r.status_code == 429:
        retry = r.headers.get("Retry-After")
        wait = float(retry) if retry and retry.isdigit() else RETRY_429_S
        return FetchResult(ok=False, error=f"rate_limited retry_after={wait}s")
    if r.status_code == 404:
        return FetchResult(ok=False, error="not_found")
    if r.status_code != 200:
        return FetchResult(ok=False, error=f"http_{r.status_code}")

    try:
        data = r.json()
    except ValueError as e:
        return FetchResult(ok=False, error=f"bad_json: {e}")

    code = data.get("pathOfBuildingExport")
    if not code:
        return FetchResult(ok=False, error="no_pathOfBuildingExport_field")

    try:
        xml = pobcode.decode(code).decode("utf-8")
    except Exception as e:
        return FetchResult(ok=False, error=f"pobcode_decode: {e}")

    # компактная мета для ML-фич (без тяжёлого gear-dump)
    # skills в ninja-API = list of {itemSlot, allGems:[{name,...}]} — берём все имена гемов
    skill_groups = []
    all_gem_names = []
    for grp in (data.get("skills") or []):
        if not isinstance(grp, dict):
            continue
        gems = [g.get("name") for g in (grp.get("allGems") or [])
                if isinstance(g, dict) and g.get("name")]
        skill_groups.append({"itemSlot": grp.get("itemSlot"), "gems": gems})
        all_gem_names.extend(gems)
    # items: имя+baseType+frameType (без модов — моды в PoB-xml)
    item_summary = []
    for it in (data.get("items") or []):
        if not isinstance(it, dict):
            continue
        idata = it.get("itemData") or {}
        item_summary.append({
            "name": idata.get("name"),
            "typeLine": idata.get("typeLine"),
            "frameType": idata.get("frameType"),
            "inventoryId": idata.get("inventoryId"),
        })
    meta = {
        "account": data.get("account", account),
        "name": data.get("name", name),
        "league": data.get("league", league),
        "level": data.get("level"),
        "class": data.get("class"),
        "ascendancy": data.get("ascendancyClassName"),
        "secondaryAscendancy": data.get("secondaryAscendancyClassName"),
        "pantheonMajor": data.get("pantheonMajor"),
        "pantheonMinor": data.get("pantheonMinor"),
        "banditChoice": data.get("banditChoice"),
        "defensiveStats": data.get("defensiveStats"),
        "keyStones": data.get("keyStones"),
        "masteriesCount": len(data.get("masteries") or []),
        "passiveCount": len(data.get("passiveSelection") or []),
        "skillGroups": skill_groups,
        "allGemNames": all_gem_names,
        "items": item_summary,
        "jewelsCount": len(data.get("jewels") or []),
        "lastCheckedUtc": data.get("lastCheckedUtc"),
    }
    return FetchResult(ok=True, pob_xml=xml, meta=meta)


def safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


def fetch_all(names_file: Path, league: str, type_: str, limit: int | None,
              force: bool, dry: bool) -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    pairs = load_pairs(names_file)
    if limit:
        pairs = pairs[:limit]

    ok = fail = cached = 0
    for i, (account, name) in enumerate(pairs, 1):
        stem = f"{safe_name(account)}__{safe_name(name)}"
        xml_path = CORPUS_DIR / f"{stem}.pob.xml"
        meta_path = CORPUS_DIR / f"{stem}.meta.json"

        if not force and xml_path.exists() and meta_path.exists():
            cached += 1
            print(f"[{i}/{len(pairs)}] CACHE {account}/{name}")
            continue

        if dry:
            print(f"[{i}/{len(pairs)}] DRY   {account}/{name}")
            continue

        print(f"[{i}/{len(pairs)}] FETCH {account}/{name} ...", end=" ", flush=True)
        res = fetch_character(account, name, league=league, type_=type_)

        if res.ok and res.pob_xml and res.meta:
            xml_path.write_text(res.pob_xml, encoding="utf-8")
            meta_path.write_text(
                json.dumps(res.meta, ensure_ascii=False, indent=2), encoding="utf-8")
            ok += 1
            print(f"OK (lvl={res.meta.get('level')} {res.meta.get('ascendancy')})")
            time.sleep(DELAY_S)
        else:
            fail += 1
            print(f"FAIL {res.error}")
            if res.error and "rate_limited" in res.error:
                wait = float(res.error.split("=")[1].rstrip("s")) if "=" in res.error else RETRY_429_S
                print(f"     sleeping {wait}s (rate limit)...")
                time.sleep(wait)

    print(f"\n=== done: ok={ok} fail={fail} cached={cached} total={len(pairs)} ===")
    print(f"corpus: {CORPUS_DIR}")


def load_pairs(path: Path) -> list[tuple[str, str]]:
    """Формат names-файла: одна пара на строку, разделитель tab/space/comma/|, или 'account/name'."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # пробуем разделители по приоритету
        for sep in ("\t", ",", "|"):
            if sep in line:
                parts = [p.strip() for p in line.split(sep, 1)]
                if len(parts) == 2 and parts[0] and parts[1]:
                    account, name = parts
                    break
        else:
            if "/" in line:
                account, name = line.rsplit("/", 1)
                account, name = account.strip(), name.strip()
            else:
                continue
        key = (account.lower(), name.lower())
        if key in seen:
            continue
        seen.add(key)
        pairs.append((account, name))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch ninja /character builds → corpus")
    ap.add_argument("names", type=Path, help="file with 'account<TAB>name' pairs (one per line)")
    ap.add_argument("--league", default="mirage")
    ap.add_argument("--type", default="exp", dest="type_")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true", help="re-fetch even if cached")
    ap.add_argument("--dry", action="store_true", help="list pairs without fetching")
    args = ap.parse_args()
    fetch_all(args.names, args.league, args.type_, args.limit, args.force, args.dry)


if __name__ == "__main__":
    main()
