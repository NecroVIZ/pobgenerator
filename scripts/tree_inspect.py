"""Временный инспектор дерева реального билда (для спайка D14)."""
from __future__ import annotations
import sys
import xml.etree.ElementTree as ET
from poebuildgen import pobcode


def main(path: str) -> None:
    xml = pobcode.decode(open(path).read().strip()).decode("utf-8")
    root = ET.fromstring(xml)
    spec = root.find("Tree/Spec")
    if spec is None:
        print("no Tree/Spec"); return
    nodes_raw = spec.get("nodes") or ""
    nodes = [n for n in nodes_raw.split(",") if n]
    build = root.find("Build")
    print(f"file:            {path}")
    print(f"class/ascend:    {build.get('className')} / {build.get('ascendClassName')}")
    print(f"classId/ascId:   {spec.get('classId')} / {spec.get('ascendClassId')}")
    print(f"treeVersion:     {spec.get('treeVersion')}")
    print(f"allocated nodes: {len(nodes)}")
    me = spec.get("masteryEffects") or ""
    print(f"masteryEffects:  {len(me.split(',')) if me else 0} entries (raw len={len(me)})")
    print(f"masteryEffects head: {me[:160]}")
    ov = spec.findall("Overrides/Override")
    print(f"overrides:       {len(ov)}")
    socks = spec.find("Sockets")
    jewels = []
    if socks is not None:
        for j in socks:
            nid = j.get("nodeId")
            if nid and j.get("itemId") and j.get("itemId") != "0":
                jewels.append((nid, j.get("itemId")))
    print(f"jewel sockets:   {len(jewels)}")
    print(f"first 12 node ids: {nodes[:12]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "builds/10.txt")
