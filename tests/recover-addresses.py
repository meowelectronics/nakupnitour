"""Recover addresses from source objects and address nodes inside shop polygons."""
import argparse
import json
import math
import re
from pathlib import Path

import osmium


parser = argparse.ArgumentParser()
parser.add_argument("--pbf", type=Path, required=True)
parser.add_argument("--sources", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
html = (root / "src" / "planner.html").read_text(encoding="utf-8")
chains = json.loads(re.search(r"const CHAINS=(.*);\nconst COUNTRIES=", html)[1])
stores = [(chain, f) for chain, data in chains.items() for f in data["stores"]["features"]]
objects = {}
for filename in (
    "lidl-czechia-osm.json", "supermarkets-czechia-osm.json",
    "supermarkets-brand-identifiers-osm.json", "billa-convenience-osm.json",
):
    source = json.loads((args.sources / filename).read_text(encoding="utf-8"))
    for element in source["elements"]:
        objects[f'{element["type"]}/{element["id"]}'] = element
target_ways = {
    int(obj.split("/")[1])
    for _, feature in stores for obj in feature["properties"]["osm_objects"].split(";")
    if obj.startswith("way/")
}
ways = {}


class Ways(osmium.SimpleHandler):
    def way(self, way):
        if way.id in target_ways:
            ways[way.id] = [n.ref for n in way.nodes]


print("Reading store building references...", flush=True)
Ways().apply_file(str(args.pbf))
needed = {node for refs in ways.values() for node in refs}
locations = {}
addresses = []
cells = {}
for _, feature in stores:
    lon, lat = feature["geometry"]["coordinates"]
    cell = math.floor(lon * 100), math.floor(lat * 100)
    cells[cell] = True


class Nodes(osmium.SimpleHandler):
    def node(self, node):
        if not node.location.valid():
            return
        lon, lat = node.location.lon, node.location.lat
        if node.id in needed:
            locations[node.id] = (lon, lat)
        if not node.tags.get("addr:housenumber"):
            return
        x, y = math.floor(lon * 100), math.floor(lat * 100)
        if any((x + dx, y + dy) in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
            addresses.append({"id": node.id, "lon": lon, "lat": lat, "tags": dict(node.tags)})


print("Reading address points and building coordinates...", flush=True)
Nodes().apply_file(str(args.pbf))


def contains(polygon, lon, lat):
    inside = False
    for a, b in zip(polygon, polygon[1:]):
        if (a[1] > lat) != (b[1] > lat):
            if lon < a[0] + (lat - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
                inside = not inside
    return inside


def formatted(tags):
    street = tags.get("addr:street") or tags.get("addr:place", "")
    number = tags.get("addr:housenumber", "")
    return ", ".join(filter(None, [
        " ".join(filter(None, [street, number])),
        tags.get("addr:city", ""), tags.get("addr:postcode", ""),
    ]))


index = {}
for address in addresses:
    cell = math.floor(address["lon"] * 100), math.floor(address["lat"] * 100)
    index.setdefault(cell, []).append(address)
recovered = []
unresolved = []
for chain, feature in stores:
    props = feature["properties"]
    candidates = []
    for obj in props["osm_objects"].split(";"):
        element = objects.get(obj)
        if element and element["tags"].get("addr:housenumber"):
            candidates.append({"address": formatted(element["tags"]), "city": element["tags"].get("addr:city", ""), "source": obj, "method": "shop-object"})
        if not obj.startswith("way/"):
            continue
        refs = ways.get(int(obj.split("/")[1]), [])
        if not refs or any(ref not in locations for ref in refs) or refs[0] != refs[-1]:
            continue
        polygon = [locations[ref] for ref in refs]
        min_x, max_x = min(p[0] for p in polygon), max(p[0] for p in polygon)
        min_y, max_y = min(p[1] for p in polygon), max(p[1] for p in polygon)
        for x in range(math.floor(min_x * 100), math.floor(max_x * 100) + 1):
            for y in range(math.floor(min_y * 100), math.floor(max_y * 100) + 1):
                for address in index.get((x, y), []):
                    if contains(polygon, address["lon"], address["lat"]):
                        candidates.append({
                            "address": formatted(address["tags"]),
                            "city": address["tags"].get("addr:city", ""),
                            "source": f'node/{address["id"]}', "building": obj,
                            "method": "address-node-inside-shop-building",
                        })
    unique = {c["address"]: c for c in candidates}
    complete = [c for c in unique.values() if c["address"] and any(ch.isdigit() for ch in c["address"])]
    if len(complete) == 1 and (not props["address"] or len(complete[0]["address"]) > len(props["address"])):
        recovered.append({"chain": chain, "store_id": props["id"], "original_address": props["address"], **complete[0]})
    elif not props["address"]:
        unresolved.append({"chain": chain, "store_id": props["id"], "objects": props["osm_objects"], "candidates": list(unique)})
result = {"source": "Local Czech OSM extract, 2026-11-07", "recovered": recovered, "unresolved": unresolved}
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Recovered {len(recovered)} addresses; {len(unresolved)} blank addresses remain.", flush=True)
for entry in recovered:
    if entry["chain"] == "lidl" and entry["store_id"] == 91:
        print(json.dumps(entry, ensure_ascii=True), flush=True)
