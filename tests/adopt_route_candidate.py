"""Rebuild improved default itineraries from verified local OSRM road geometry."""

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

from optimize_kaufland import path_cost


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, action="append", required=True)
    parser.add_argument("--road-data", type=Path, required=True)
    parser.add_argument("--osrm-packages", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / "src" / "planner.html"
    html = source.read_text(encoding="utf-8")
    line = next(line for line in html.splitlines() if line.startswith("const CHAINS="))
    chains = json.loads(line.removeprefix("const CHAINS=").removesuffix(";"))
    sys.path.insert(0, str(args.osrm_packages))
    import osrm

    os.chdir(args.road_data)
    engine = osrm.OSRM(storage_config="czech-republic-latest.osrm", algorithm="MLD",
                       use_shared_memory=False)
    for result_path in args.result:
        evidence = json.loads(result_path.read_text(encoding="utf-8"))
        slug = evidence["chain"].casefold()
        chain = chains[slug]
        features = chain["stores"]["features"]
        coordinates = [tuple(f["geometry"]["coordinates"]) for f in features]
        signature = hashlib.sha256(json.dumps(coordinates).encode()).hexdigest()
        matrix_path = args.road_data / f"{slug}-driving-matrix.json"
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        if signature != evidence["matrix_coordinate_sha256"] or signature != matrix["coordinate_sha256"]:
            raise ValueError(f"{slug}: coordinate mismatch.")
        if hashlib.sha256(matrix_path.read_bytes()).hexdigest() != evidence["matrix_file_sha256"]:
            raise ValueError(f"{slug}: matrix mismatch.")
        ids = [f["properties"]["id"] for f in features]
        order_ids = evidence["order_ids"]
        if len(ids) != len(set(ids)) or len(order_ids) != len(ids) or set(order_ids) != set(ids):
            raise ValueError(f"{slug}: invalid shop permutation.")
        by_id = {store_id: i for i, store_id in enumerate(ids)}
        order = [by_id[i] for i in order_ids]
        cost = np.rint(np.asarray(matrix["durations"]) * 10).astype(np.int64)
        seconds = path_cost(cost, order) / 10
        old = chain["optimized"]
        if abs(seconds - evidence["best_driving_seconds"]) > 1e-6:
            raise ValueError(f"{slug}: candidate objective mismatch.")
        if evidence["road_data_timestamp"] != old["metadata"]["road_data_timestamp"]:
            raise ValueError(f"{slug}: road snapshot mismatch.")
        if seconds >= old["driving_seconds"] - .11:
            raise ValueError(f"{slug}: candidate is not a strict improvement.")
        updated = copy.deepcopy(old)
        legs, cumulative_seconds, cumulative_metres = [], [0.0], [0.0]
        distance_ties = []
        for a, b in zip(order, order[1:]):
            response = engine.Route(osrm.RouteParameters(
                coordinates=[coordinates[a], coordinates[b]], geometries="geojson",
                overview="full", continue_straight=False))
            if response["code"] != "Ok" or len(response["routes"]) != 1:
                raise RuntimeError(f"{slug}: OSRM failed for {a} -> {b}.")
            route = response["routes"][0]
            duration, distance = float(route["duration"]), float(route["distance"])
            shape = [[float(c[0]), float(c[1])] for c in route["geometry"]["coordinates"]]
            if abs(duration - matrix["durations"][a][b]) > .11 or len(shape) < 2:
                raise RuntimeError(f"{slug}: road geometry/matrix disagreement.")
            if abs(distance - matrix["distances"][a][b]) > .11:
                if abs(duration - matrix["durations"][a][b]) > 1e-6:
                    raise RuntimeError(f"{slug}: differing road distance is not an exact modeled-time tie.")
                distance_ties.append({"from_store_id": ids[a], "to_store_id": ids[b],
                                      "matrix_metres": matrix["distances"][a][b],
                                      "route_metres": distance, "duration_seconds": duration})
                print(f"{slug}: exact-time tie {ids[a]} -> {ids[b]}: road geometry {distance}m, "
                      f"matrix {matrix['distances'][a][b]}m; retaining actual road geometry distance.", flush=True)
            legs.append({"duration_seconds": duration, "distance_metres": distance, "coordinates": shape})
            cumulative_seconds.append(cumulative_seconds[-1] + duration)
            cumulative_metres.append(cumulative_metres[-1] + distance)
        if abs(cumulative_seconds[-1] - seconds) > .11:
            raise RuntimeError(f"{slug}: rebuilt total disagrees with candidate.")
        updated.update(order_ids=order_ids, legs=legs, cumulative_seconds=cumulative_seconds,
                       cumulative_metres=cumulative_metres, driving_seconds=cumulative_seconds[-1],
                       distance_metres=cumulative_metres[-1],
                       snapping=[{"store_id": ids[i], **matrix["snapped"][i]} for i in order])
        lower = evidence["lower_bound_seconds"]
        updated["metadata"].update(
            algorithm=evidence["solver"],
            optimality=("Numerically optimal for the static matrix" if evidence["proven_optimal_for_integer_matrix"]
                        else "Best found, not proven globally optimal"),
            rounded_objective_seconds=seconds,
            assignment_lower_bound_seconds=evidence["assignment_lower_bound_seconds"],
            lower_bound_seconds=lower, upper_to_lower_bound_gap_percent=100 * (seconds / lower - 1),
            search_wall_seconds=evidence["total_wall_seconds"])
        updated["metadata"].pop("attempts", None)
        updated["metadata"]["heuristic_runs"] = evidence.get("heuristic_runs", 1)
        updated["metadata"]["equal_time_distance_differences"] = distance_ties
        chain["optimized"] = updated
        print(f"{slug}: rebuilt {len(legs)} road legs; saved {old['driving_seconds'] - seconds:.1f}s", flush=True)
    if source.read_text(encoding="utf-8") != html:
        raise RuntimeError("Planner changed during geometry rebuild; refusing to overwrite.")
    source.write_text(html.replace(line, "const CHAINS=" + json.dumps(chains, ensure_ascii=False, separators=(",", ":")) + ";", 1),
                      encoding="utf-8")


if __name__ == "__main__":
    main()
