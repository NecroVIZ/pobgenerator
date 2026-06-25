"""Harvest {account,name} pairs из ninja /poe1/builds/mirage (и по class-фильтрам) через playwright.

Рендерит страницу, scroll'ит для подгрузки (lazy-pagination), собирает все /character/ URL'ы,
дедуплит, пишет в corpus_seed.txt (append, ничего не удаляет).

Стратификация: прогоняется по списку class-фильтров (по ascendancy), чтобы покрыть разные
архетипы, а не только топ-DPS билды с главной.
"""
from __future__ import annotations

import argparse
import re
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://poe.ninja/poe1/builds/mirage"
# ascendancy-фильтры для стратификации (покрыть классы/архетипы)
CLASSES = [
    "",  # без фильтра = топ-DPS разнообразный
    "?class=Elementalist", "?class=Necromancer", "?class=Occultist",
    "?class=Inquisitor", "?class=Hierophant", "?class=Guardian", "?class=Templar",
    "?class=Assassin", "?class=Trickster", "?class=Shadow",
    "?class=Deadeye", "?class=Raider", "?class=Pathfinder", "?class=Ranger",
    "?class=Juggernaut", "?class=Berserker", "?class=Chieftain", "?class=Marauder",
    "?class=Slayer", "?class=Gladiator", "?class=Champion", "?class=Duelist",
    "?class=Ascendant", "?class=Saboteur",
]
SCROLL_PAUSE_MS = 1500
MAX_SCROLLS = 8            # достаточно для ~несколько сотен ссылок
SEED_FILE = Path(__file__).resolve().parents[1] / "corpus_seed.txt"

CHAR_RE = re.compile(r"/character/([^/?]+)/([^?\"'\s<>]+)")


def harvest_class(page, class_filter: str) -> set[tuple[str, str]]:
    url = BASE + class_filter
    print(f"  -> {url}")
    page.goto(url, timeout=60000, wait_until="networkidle")
    page.wait_for_timeout(2500)

    seen: set[tuple[str, str]] = set()
    last_count = -1
    for scroll_i in range(MAX_SCROLLS):
        # собрать ссылки
        hrefs = page.eval_on_selector_all(
            "a[href*='/character/']",
            "els => els.map(e => e.getAttribute('href'))",
        )
        new = 0
        for h in hrefs:
            m = CHAR_RE.search(h or "")
            if not m:
                continue
            account_raw, name_raw = m.group(1), m.group(2)
            # URL-decode (китайские/корейские/unicode имена)
            account = urllib.parse.unquote(account_raw)
            name = urllib.parse.unquote(name_raw)
            if (account, name) not in seen:
                seen.add((account, name))
                new += 1
        if len(seen) == last_count and scroll_i > 1:
            # нет новых после скролла — конец пагинации
            break
        last_count = len(seen)
        # скролл вниз для подгрузки
        page.mouse.wheel(0, 8000)
        page.wait_for_timeout(SCROLL_PAUSE_MS)
    print(f"     {len(seen)} unique pairs (after {MAX_SCROLLS} scrolls)")
    return seen


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", nargs="*", default=None,
                    help="subset of class-filters (default: all)")
    ap.add_argument("--max-scrolls", type=int, default=MAX_SCROLLS)
    ap.add_argument("--limit-per-class", type=int, default=None)
    args = ap.parse_args()

    classes = args.classes if args.classes is not None else CLASSES
    # существующие пары (не дублировать)
    existing: set[tuple[str, str]] = set()
    if SEED_FILE.exists():
        for line in SEED_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for sep in ("\t", ",", "|"):
                if sep in line:
                    parts = line.split(sep, 1)
                    if len(parts) == 2:
                        existing.add((parts[0].strip(), parts[1].strip()))
                        break
            else:
                if "/" in line:
                    a, n = line.rsplit("/", 1)
                    existing.add((a.strip(), n.strip()))
    print(f"existing pairs in {SEED_FILE.name}: {len(existing)}")

    all_new: set[tuple[str, str]] = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        for cf in classes:
            try:
                pairs = harvest_class(page, cf)
            except Exception as e:
                print(f"  ERROR on {cf}: {e}")
                continue
            fresh = pairs - existing
            if args.limit_per_class:
                fresh = set(list(fresh)[:args.limit_per_class])
            all_new |= fresh
            time.sleep(1.0)  # вежливая пауза между class-страницами
        browser.close()

    print(f"\n=== harvested {len(all_new)} NEW unique pairs across {len(classes)} class-filters ===")

    # append к seed-файлу
    header = f"\n# === playwright harvest {time.strftime('%Y-%m-%d %H:%M')} ({len(all_new)} new) ===\n"
    with open(SEED_FILE, "a", encoding="utf-8") as f:
        f.write(header)
        for account, name in sorted(all_new):
            f.write(f"{account}\t{name}\n")
    print(f"appended to {SEED_FILE}")
    print(f"total in seed now: {len(existing) + len(all_new)}")


if __name__ == "__main__":
    main()
