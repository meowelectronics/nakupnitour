"""Verify solver evidence against the current planner before generating page data."""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from optimize_kaufland import path_cost


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--matrices", type=Path, required=True)
    parser.add_argument("--additional-result", type=Path, action="append", default=[])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    html = (root / "src" / "planner.html").read_text(encoding="utf-8")
    line = next(line for line in html.splitlines() if line.startswith("const CHAINS="))
    chains = json.loads(line.removeprefix("const CHAINS=").removesuffix(";"))
    reports = []
    additional = [json.loads(p.read_text(encoding="utf-8")) for p in args.additional_result]
    if any(e["chain"].casefold() not in ("lidl", "kaufland", "billa", "albert") for e in additional):
        raise ValueError("Additional result has an unknown chain.")
    for slug in ["lidl", "kaufland", "billa", "albert"]:
        results = [json.loads((args.results / f"{slug}-optimality.json").read_text(encoding="utf-8"))]
        results.extend(e for e in additional if e["chain"].casefold() == slug)
        matrix_path = args.matrices / f"{slug}-driving-matrix.json"
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        chain = chains[slug]
        features, published = chain["stores"]["features"], chain["optimized"]
        signature = hashlib.sha256(json.dumps([tuple(f["geometry"]["coordinates"]) for f in features]).encode()).hexdigest()
        if signature != matrix["coordinate_sha256"]:
            raise ValueError(f"{slug}: coordinate fingerprints disagree.")
        matrix_hash = hashlib.sha256(matrix_path.read_bytes()).hexdigest()
        ids = [f["properties"]["id"] for f in features]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{slug}: duplicate store IDs.")
        by_id = {store_id: index for index, store_id in enumerate(ids)}
        cost = np.rint(np.asarray(matrix["durations"]) * 10).astype(np.int64)
        published_order = published["order_ids"]
        if len(published_order) != len(ids) or set(published_order) != set(ids):
            raise ValueError(f"{slug}: invalid published permutation.")
        upper = path_cost(cost, [by_id[i] for i in published_order]) / 10
        if abs(upper - published["driving_seconds"]) > 0.11:
            raise ValueError(f"{slug}: published route objective disagrees with matrix.")
        for result in results:
            if signature != result["matrix_coordinate_sha256"] or matrix_hash != result["matrix_file_sha256"]:
                raise ValueError(f"{slug}: evidence fingerprints disagree.")
            if result["store_count"] != len(ids):
                raise ValueError(f"{slug}: store count changed.")
            order = result["order_ids"]
            if len(order) != len(ids) or set(order) != set(ids):
                raise ValueError(f"{slug}: invalid shop permutation.")
            actual = path_cost(cost, [by_id[i] for i in order]) / 10
            if abs(actual - result["best_driving_seconds"]) > 1e-6:
                raise ValueError(f"{slug}: route objective disagrees with matrix.")
            if not (0 < result["lower_bound_seconds"] <= result["best_driving_seconds"] <= result["original_driving_seconds"]):
                raise ValueError(f"{slug}: invalid bounds.")
            if result["road_data_timestamp"] != published["metadata"]["road_data_timestamp"]:
                raise ValueError(f"{slug}: road snapshot changed.")
        evidence = max(results, key=lambda e: (e["lower_bound_seconds"],
                                               not e["solver"].startswith("LKH"),
                                               e["generated_at"]))
        lower = evidence["lower_bound_seconds"]
        best = min(upper, *(e["best_driving_seconds"] for e in results))
        if lower > best:
            raise ValueError(f"{slug}: combined lower bound exceeds verified candidate.")
        gap = 100 * (upper / lower - 1)
        report = {
            "slug": slug, "name": chain["name"], "stores": len(ids),
            "checkedAt": max(e["generated_at"] for e in results),
            "publishedSeconds": upper, "lowerBoundSeconds": lower,
            "savedSinceInitialCheckSeconds": max(e["original_driving_seconds"] for e in results) - upper,
            "publishedGapPercent": gap,
            "displayGapPercent": math.ceil(gap * 1000 - 1e-9) / 1000,
            "maximumSavingSeconds": upper - lower,
            "underThirtyMinutes": upper - lower < 1800,
            "bestCandidateSeconds": best,
            "candidateGapPercent": 100 * (best / lower - 1),
            "candidateMaximumSavingSeconds": best - lower,
            "candidateImprovementSeconds": upper - best,
            "publishedOptimal": upper == lower,
            "solverSeconds": evidence["wall_seconds"],
            "exactBudgetSeconds": evidence["exact_solver_budget_seconds"],
            "heuristicBudgetSeconds": evidence["heuristic_budget_seconds"],
            "heuristicRuns": evidence.get("heuristic_runs", 1),
            "roadSnapshot": evidence["road_data_timestamp"],
            "matrixSha256": evidence["matrix_file_sha256"],
            "coordinateSha256": signature,
            "publishedOrderSha256": hashlib.sha256(json.dumps(published["order_ids"]).encode()).hexdigest(),
            "solver": evidence["solver"],
            "thresholdCertificate": evidence.get("threshold_infeasible", False),
            "certificateModelSha256": evidence.get("certificate_model_sha256"),
            "searches": [{"solver": e["solver"], "seconds": e["total_wall_seconds"],
                          "bestSeconds": e["best_driving_seconds"],
                          "lowerSeconds": e["lower_bound_seconds"]}
                         for e in results],
        }
        reports.append(report)
        print(f"{slug}: published gap <= {report['displayGapPercent']:.3f}%; candidate saving {upper-best:.1f}s")
    output = root / "src" / "data" / "route-accuracy.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
