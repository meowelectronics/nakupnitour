"""Query Overpass for address points and buildings around unresolved shops."""
import json
import math
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
recovery = json.loads((ROOT / "address-recovery.json").read_text(encoding="utf-8"))
chains = json.loads(re.search(
    r"const CHAINS=(.*);\nconst COUNTRIES=",
    (ROOT / "src" / "planner.html").read_text(encoding="utf-8"),
)[1])
unresolved = recovery["unresolved"]
groups = {"node": set(), "way": set(), "relation": set()}
for entry in unresolved:
    for obj in entry["objects"].split(";"):
        kind, identifier = obj.split("/")
        groups[kind].add(identifier)
selections = "".join(f'{kind}(id:{",".join(sorted(ids))});' for kind, ids in groups.items() if ids)
query = (
    '[out:json][timeout:180];(' + selections + ')->.shops;'
    '(node(around.shops:80)["addr:housenumber"];'
    'way(around.shops:30)["building"];.shops;);out body geom;'
)
(ROOT / "overpass-address-query.txt").write_text(query, encoding="utf-8")
cache = ROOT / "overpass-addresses.json"
if not cache.exists():
    request = Request(
        "https://overpass-api.de/api/interpreter",
        data=urlencode({"data": query}).encode(),
        headers={"User-Agent": "NakupniTour address verification", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=240) as response:
        payload = json.load(response)
    if payload.get("remark"):
        raise RuntimeError(payload["remark"])
    cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
payload = json.loads(cache.read_text(encoding="utf-8"))
elements = {f'{e["type"]}/{e["id"]}': e for e in payload["elements"]}
points = [e for e in elements.values() if e["type"] == "node" and e.get("tags", {}).get("addr:housenumber")]
buildings = [e for e in elements.values() if e["type"] == "way" and e.get("tags", {}).get("building") and e.get("geometry")]


def inside(geometry, lon, lat):
    contained = False
    for a, b in zip(geometry, geometry[1:]):
        ax, ay, bx, by = a["lon"], a["lat"], b["lon"], b["lat"]
        dx, dy = bx - ax, by - ay
        scale = math.cos(math.radians(lat))
        denom = (dx * scale) ** 2 + dy ** 2
        t = min(1, max(0, (((lon - ax) * scale) * dx * scale + (lat - ay) * dy) / denom)) if denom else 0
        if math.hypot((lon - ax - t * dx) * scale, lat - ay - t * dy) < 1e-6:
            return True
        if (ay > lat) != (by > lat) and lon < ax + (lat - ay) * dx / dy:
            contained = not contained
    return contained


def formatted(tags):
    return ", ".join(filter(None, [
        " ".join(filter(None, [tags.get("addr:street") or tags.get("addr:place"), tags.get("addr:housenumber")])),
        tags.get("addr:city"), tags.get("addr:postcode"),
    ]))


remaining = []
for entry in unresolved:
    feature = next(f for f in chains[entry["chain"]]["stores"]["features"] if f["properties"]["id"] == entry["store_id"])
    lon, lat = feature["geometry"]["coordinates"]
    own = entry["objects"].split(";")
    candidates = []
    def candidate(obj, method, building=None):
        tags = obj.get("tags", {})
        if tags.get("addr:housenumber"):
            candidates.append({
                "address": formatted(tags), "city": tags.get("addr:city", ""),
                "source": f'{obj["type"]}/{obj["id"]}', "method": method,
                **({"building": building} if building else {}),
            })
    for obj in own:
        if obj in elements:
            candidate(elements[obj], "overpass-shop-object")
    enclosing = [b for b in buildings if f'way/{b["id"]}' in own or inside(b["geometry"], lon, lat)]
    for building in enclosing:
        identifier = f'way/{building["id"]}'
        candidate(building, "overpass-enclosing-building")
        for point in points:
            if inside(building["geometry"], point["lon"], point["lat"]):
                candidate(point, "overpass-address-node-in-building", identifier)
    direct = [c for c in candidates if c["method"] == "overpass-shop-object"]
    if direct:
        candidates = direct
    unique = {c["address"]: c for c in candidates}
    # Missing city/postcode on duplicate objects does not make an address ambiguous.
    most_complete = [
        c for text, c in unique.items()
        if not any(other.startswith(text + ", ") for other in unique if other != text)
    ]
    unique = {c["address"]: c for c in most_complete}
    if len(unique) == 1:
        recovery["recovered"].append({
            "chain": entry["chain"], "store_id": entry["store_id"],
            "original_address": feature["properties"]["address"], **next(iter(unique.values())),
        })
    else:
        remaining.append({**entry, "candidates": list(unique)})
recovery["unresolved"] = remaining
recovery["overpass_snapshot"] = payload.get("osm3s", {}).get("timestamp_osm_base")
(ROOT / "address-recovery.json").write_text(json.dumps(recovery, ensure_ascii=False, indent=2), encoding="utf-8")
print(f'Overpass snapshot: {recovery["overpass_snapshot"]}; {len(elements)} objects.')
print(f'Total recovered: {len(recovery["recovered"])}; unresolved blank addresses: {len(remaining)}.')
