"""Bounded directed open-path experiment for any chain; never changes route data."""

import argparse
import hashlib
import json
import math
import sys
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linear_sum_assignment, milp
from scipy.sparse import coo_matrix
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import breadth_first_order, maximum_flow


def path_cost(cost, order):
    return sum(int(cost[a, b]) for a, b in zip(order, order[1:]))


def cycles_from_successors(successors):
    if sorted(successors) != list(range(len(successors))):
        raise ValueError("Selected arcs are not a permutation.")
    unseen = set(range(len(successors)))
    cycles = []
    while unseen:
        start = min(unseen)
        cycle, node = [], start
        while node in unseen:
            unseen.remove(node)
            cycle.append(node)
            node = successors[node]
        if node != start:
            raise ValueError("Selected arcs do not form disjoint cycles.")
        cycles.append(cycle)
    return cycles


def open_order(successors, dummy):
    cycles = cycles_from_successors(successors)
    if len(cycles) != 1:
        raise ValueError("Disconnected cycle cover is not a valid itinerary.")
    order, node = [], successors[dummy]
    while node != dummy:
        order.append(node)
        node = successors[node]
    return order


def patch_cycle_cover(cost, successors):
    successors = list(successors)
    while True:
        cycles = cycles_from_successors(successors)
        if len(cycles) == 1:
            return open_order(successors, len(cost) - 1)
        labels = np.empty(len(cost), dtype=int)
        for label, cycle in enumerate(cycles):
            labels[cycle] = label
        following = np.asarray(successors)
        # Swapping successors between different cycles joins them into one.
        delta = (cost[:, following] + cost[:, following].T
                 - cost[np.arange(len(cost)), following][:, None]
                 - cost[np.arange(len(cost)), following][None, :])
        delta[labels[:, None] == labels[None, :]] = np.iinfo(np.int64).max
        a, b = np.unravel_index(np.argmin(delta), delta.shape)
        successors[a], successors[b] = successors[b], successors[a]


def improve_route(cost, order, seconds, package_path):
    if seconds <= 0:
        return order
    if package_path:
        sys.path.insert(0, str(package_path))
    from ortools.constraint_solver import pywrapcp

    dummy = len(order)
    manager = pywrapcp.RoutingIndexManager(dummy + 1, 1, dummy)
    routing = pywrapcp.RoutingModel(manager)
    transit = routing.RegisterTransitMatrix(cost.tolist())
    routing.SetArcCostEvaluatorOfAllVehicles(transit)
    parameters = pywrapcp.DefaultRoutingSearchParameters()
    from ortools.constraint_solver import routing_enums_pb2

    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.seconds = seconds
    initial = routing.ReadAssignmentFromRoutes([order], True)
    if initial is None:
        raise RuntimeError("OR-Tools rejected the verified incumbent.")
    solution = routing.SolveFromAssignmentWithParameters(initial, parameters)
    if solution is None:
        raise RuntimeError("OR-Tools returned no solution.")
    improved, index = [], routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != dummy:
            improved.append(node)
        index = solution.Value(routing.NextVar(index))
    if sorted(improved) != list(range(dummy)):
        raise RuntimeError("Improved path is missing or duplicating shops.")
    if path_cost(cost, improved) != solution.ObjectiveValue():
        raise RuntimeError("OR-Tools objective disagrees with the verified path.")
    return improved if path_cost(cost, improved) < path_cost(cost, order) else order


def solve_lkh(cost, initial_order, seconds, lower_bound, runs, seed=20261109):
    from elkai import _elkai

    started = time.monotonic()
    size = len(cost)
    # LKH expects positive weights. Adding one to every arc adds exactly
    # size to every tour, preserving the directed open-path objective.
    shifted = cost + 1
    problem = (f"TYPE : ATSP\nDIMENSION : {size}\nEDGE_WEIGHT_TYPE : EXPLICIT\n"
               "EDGE_WEIGHT_FORMAT : FULL_MATRIX\nEDGE_WEIGHT_SECTION\n"
               + "\n".join(" ".join(map(str, row)) for row in shifted) + "\n")
    parameters = (f"RUNS = {runs}\nTIME_LIMIT = {max(1, seconds // runs)}\n"
                  "MAX_TRIALS = 10000\nMOVE_TYPE = 5\nPATCHING_C = 3\nPATCHING_A = 2\n"
                  f"MAX_CANDIDATES = 20\nSEED = {seed}\nPROBLEM_FILE = :stdin:\n")
    with tempfile.TemporaryDirectory(prefix="nakupnitour-lkh-") as work:
        tour_file = Path(work) / "incumbent.tour"
        tour_file.write_text(
            f"TYPE : TOUR\nDIMENSION : {size}\nTOUR_SECTION\n"
            + "\n".join(str(i + 1) for i in [size - 1, *initial_order]) + "\n-1\nEOF\n",
            encoding="ascii")
        parameters += f"INITIAL_TOUR_FILE = {tour_file}\n"
        tour = [int(i) - 1 for i in _elkai.solve_problem(parameters, problem)]
    if sorted(tour) != list(range(size)):
        raise RuntimeError("LKH returned an incomplete or duplicated tour.")
    pivot = tour.index(size - 1)
    candidate = tour[pivot + 1:] + tour[:pivot]
    best_order = candidate if path_cost(cost, candidate) < path_cost(cost, initial_order) else list(initial_order)
    upper = path_cost(cost, best_order)
    if lower_bound > upper:
        raise RuntimeError("LKH candidate violates the prior bound.")
    assignment = cost.astype(float)
    np.fill_diagonal(assignment, np.inf)
    a, b = linear_sum_assignment(assignment)
    assignment_bound = int(cost[a, b].sum())
    lower_bound = max(lower_bound, assignment_bound)
    return {"order_indices": best_order, "lower_bound_seconds": lower_bound / 10,
            "assignment_lower_bound_seconds": assignment_bound / 10,
            "best_driving_seconds": upper / 10, "proven_optimal_for_integer_matrix": upper == lower_bound,
            "gap_percent_of_lower_bound": 100 * (upper / lower_bound - 1),
            "wall_seconds": time.monotonic() - started, "lkh_runs": runs, "lkh_seed": seed}


def fractional_subtours(values, rows, cols, size):
    """Find valid violated cuts using directed min-cuts rooted at the dummy."""
    capacity = csr_matrix((np.floor(np.clip(values, 0, 1) * 1_000_000).astype(np.int64),
                           (rows, cols)), shape=(size, size))
    capacity.eliminate_zeros()
    found = set()
    for target in range(size - 1):
        flow = maximum_flow(capacity, size - 1, target)
        if flow.flow_value >= 1_000_000:
            continue
        residual = capacity - flow.flow
        residual.data = (residual.data > 0).astype(np.int64)
        residual.eliminate_zeros()
        reachable = breadth_first_order(residual, size - 1, directed=True, return_predecessors=False)
        inside = np.zeros(size, dtype=bool)
        inside[reachable] = True
        if values[inside[rows] & ~inside[cols]].sum() >= 1 - 1e-7:
            continue
        # Complement cuts are equivalent under the degree equalities.
        subset = frozenset(np.flatnonzero(inside if inside.sum() <= size / 2 else ~inside))
        if len(subset) > 1:
            found.add(subset)
    return found


def blossom_inequalities(values, rows, cols, size):
    """Separate odd-cut inequalities in the undirected degree-two projection."""
    import networkx as nx

    directed = np.zeros((size, size))
    directed[rows, cols] = values
    undirected = directed + directed.T
    high = undirected > .5
    graph = nx.Graph()
    graph.add_nodes_from(range(size))
    for a in range(size):
        for b in range(a + 1, size):
            capacity = max(0, math.floor(min(undirected[a, b], 1 - undirected[a, b]) * 1_000_000))
            if capacity or a == 0:
                graph.add_edge(a, b, capacity=capacity)
    tree = nx.gomory_hu_tree(graph, capacity="capacity")
    found = []
    for a, b, data in list(tree.edges(data=True)):
        if data["weight"] >= 1_000_000:
            continue
        weight = data["weight"]
        tree.remove_edge(a, b)
        subset = frozenset(nx.node_connected_component(tree, a))
        tree.add_edge(a, b, weight=weight)
        inside = np.zeros(size, dtype=bool)
        inside[list(subset)] = True
        crossing = inside[:, None] != inside[None, :]
        selected = np.triu(crossing & high)
        count = int(selected.sum())
        if count % 2 != 1:
            continue
        slack = undirected[np.triu(crossing & ~high)].sum() + (1 - undirected[selected]).sum()
        if slack < 1 - 1e-7:
            positive = (inside[rows] != inside[cols]) & ~high[rows, cols]
            negative = (inside[rows] != inside[cols]) & high[rows, cols]
            indices = np.flatnonzero(positive | negative)
            coefficients = np.where(positive[indices], 1.0, -1.0)
            found.append((indices, coefficients, 1 - count))
    return found


def solve_circuit(cost, initial_order, seconds, lower_bound, package_path, target_seconds=0, linearization=2):
    if package_path:
        sys.path.insert(0, str(package_path))
    from ortools.sat.python import cp_model

    started = time.monotonic()
    size = len(cost)
    dummy = size - 1
    model = cp_model.CpModel()
    edges = [(a, b, model.new_bool_var(f"a{a}_{b}"))
             for a in range(size) for b in range(size) if a != b]
    model.add_circuit(edges)
    objective = sum(int(cost[a, b]) * variable for a, b, variable in edges)
    upper = path_cost(cost, initial_order)
    model.add(objective <= upper)
    model.add(objective >= lower_bound)
    model.minimize(objective)
    seed = [dummy, *initial_order, dummy]
    selected = set(zip(seed, seed[1:]))
    for a, b, variable in edges:
        model.add_hint(variable, int((a, b) in selected))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(.01, seconds - (time.monotonic() - started))
    solver.parameters.num_search_workers = 2
    solver.parameters.linearization_level = linearization
    solver.parameters.random_seed = 20261109

    class Progress(cp_model.CpSolverSolutionCallback):
        def on_solution_callback(self):
            print(json.dumps({"phase": "CP-SAT circuit", "incumbent_seconds": self.objective_value / 10,
                              "lower_bound_seconds": self.best_objective_bound / 10}), flush=True)
            if target_seconds > 0 and self.objective_value - self.best_objective_bound < target_seconds * 10:
                self.stop_search()

    status = solver.solve(model, Progress())
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE, cp_model.UNKNOWN):
        raise RuntimeError(f"CP-SAT failed: {solver.status_name(status)}")
    best_order = list(initial_order)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        successors = [-1] * size
        for a, b, variable in edges:
            if solver.boolean_value(variable):
                successors[a] = b
        candidate = open_order(successors, dummy)
        if abs(path_cost(cost, candidate) - solver.objective_value) > .01:
            raise RuntimeError("CP-SAT objective disagrees with reconstructed route.")
        if path_cost(cost, candidate) < upper:
            best_order = candidate
    lower_bound = max(lower_bound, math.ceil(solver.best_objective_bound - 1e-4))
    upper = path_cost(cost, best_order)
    if lower_bound > upper:
        raise RuntimeError("CP-SAT lower bound exceeds verified candidate.")
    assignment = cost.astype(float)
    np.fill_diagonal(assignment, np.inf)
    a, b = linear_sum_assignment(assignment)
    return {
        "order_indices": best_order, "lower_bound_seconds": lower_bound / 10,
        "assignment_lower_bound_seconds": int(cost[a, b].sum()) / 10,
        "best_driving_seconds": upper / 10, "proven_optimal_for_integer_matrix": lower_bound == upper,
        "gap_percent_of_lower_bound": 100 * (upper / lower_bound - 1) if lower_bound else 0,
        "wall_seconds": time.monotonic() - started, "solver_status": solver.status_name(status),
    }


def solve_incremental(cost, initial_order, seconds, lower_bound, target_seconds=0, checkpoint=None):
    """Keep the LP basis and all subtour cuts across solves instead of restarting."""
    import highspy

    started = time.monotonic()
    size = len(cost)
    rows, cols = np.where(~np.eye(size, dtype=bool))
    objective = cost[rows, cols].astype(float)
    count = len(rows)
    upper = path_cost(cost, initial_order)
    assignment = cost.astype(float)
    np.fill_diagonal(assignment, np.inf)
    a, b = linear_sum_assignment(assignment)
    assignment_bound = int(cost[a, b].sum())
    lower_bound = max(lower_bound, assignment_bound)
    matrix = coo_matrix((np.ones(count * 2),
                         (np.concatenate((rows, size + cols)), np.tile(np.arange(count), 2))),
                        shape=(2 * size, count)).tocsc()
    lp = highspy.HighsLp()
    lp.num_col_, lp.num_row_ = count, 2 * size
    lp.col_cost_, lp.col_lower_, lp.col_upper_ = objective, np.zeros(count), np.ones(count)
    lp.row_lower_, lp.row_upper_ = np.ones(2 * size), np.ones(2 * size)
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = matrix.indptr, matrix.indices, matrix.data
    solver = highspy.Highs()
    solver.setOptionValue("output_flag", False)
    solver.setOptionValue("threads", 2)
    solver.setOptionValue("solver", "simplex")
    if solver.passModel(lp) != highspy.HighsStatus.kOk:
        raise RuntimeError("HiGHS rejected incremental LP.")
    cuts, history = set(), []
    best_order = list(initial_order)
    linear = True
    decision_limit = None
    while time.monotonic() - started < seconds and lower_bound < upper:
        solver.setOptionValue("time_limit", solver.getRunTime() + max(.01, seconds - (time.monotonic() - started)))
        solver.run()
        status = solver.getModelStatus()
        if status == highspy.HighsModelStatus.kInfeasible and decision_limit is not None:
            # Every globally feasible tour below this threshold was retained.
            lower_bound = max(lower_bound, decision_limit + 1)
            print(json.dumps({"phase": "threshold infeasibility certificate",
                              "lower_bound_seconds": lower_bound / 10}), flush=True)
            break
        if status not in (highspy.HighsModelStatus.kOptimal, highspy.HighsModelStatus.kTimeLimit):
            raise RuntimeError(f"Incremental HiGHS failed: {solver.modelStatusToString(status)}")
        info, solution = solver.getInfo(), solver.getSolution()
        if linear and status != highspy.HighsModelStatus.kOptimal:
            break
        dual = info.objective_function_value if linear else info.mip_dual_bound
        if math.isfinite(dual):
            certified = math.ceil(dual - 1e-4)
            if decision_limit is not None:
                certified = min(certified, decision_limit + 1)
            lower_bound = max(lower_bound, certified)
        values = np.asarray(solution.col_value) if solution.value_valid else None
        new_cuts = set()
        if values is not None:
            if linear:
                new_cuts = fractional_subtours(values, rows, cols, size) - cuts
            if np.max(np.abs(values - np.rint(values))) < 1e-6:
                successors = [-1] * size
                for index in np.flatnonzero(values > .5):
                    if successors[rows[index]] != -1:
                        raise RuntimeError("Incremental HiGHS selected duplicate successors.")
                    successors[rows[index]] = int(cols[index])
                cycles = cycles_from_successors(successors)
                if len(cycles) == 1:
                    candidate = open_order(successors, size - 1)
                    value = path_cost(cost, candidate)
                    if abs(value - info.objective_function_value) > .01:
                        raise RuntimeError("Incremental objective mismatch.")
                    if value < upper:
                        upper, best_order = value, candidate
                else:
                    candidate = patch_cycle_cover(cost, successors)
                    if path_cost(cost, candidate) < upper:
                        upper, best_order = path_cost(cost, candidate), candidate
                    new_cuts.update(frozenset(c) for c in cycles if frozenset(c) not in cuts)
            elif linear and not new_cuts:
                weights = np.full((size, size), np.inf)
                weights[rows, cols] = -values * (float(cost.max()) + 1) + objective / size
                a, b = linear_sum_assignment(weights)
                successors = [-1] * size
                for source, target in zip(a, b):
                    successors[source] = int(target)
                candidate = patch_cycle_cover(cost, successors)
                if path_cost(cost, candidate) < upper:
                    upper, best_order = path_cost(cost, candidate), candidate
        if lower_bound > upper:
            raise RuntimeError("Incremental lower bound exceeds feasible route.")
        history.append({"elapsed_seconds": time.monotonic() - started,
                        "lower_bound_seconds": lower_bound / 10, "incumbent_seconds": upper / 10,
                        "phase": "warm-start LP" if linear else "warm-start MILP", "subtour_cuts": len(cuts)})
        print(json.dumps(history[-1]), flush=True)
        if target_seconds > 0 and upper - lower_bound < target_seconds * 10:
            break
        ready_for_decision = (linear and target_seconds > 0
                              and info.objective_function_value >= upper - target_seconds * 10 - 3000)
        if ready_for_decision:
            new_cuts = set()
        if linear and not ready_for_decision and (not new_cuts or len(history) % 5 == 0):
            blossoms = blossom_inequalities(values, rows, cols, size)
            for indices, coefficients, lower in blossoms:
                if solver.addRow(lower, np.inf, len(indices), indices.astype(np.int32),
                                 coefficients) != highspy.HighsStatus.kOk:
                    raise RuntimeError("HiGHS rejected odd-cut inequality.")
            if blossoms:
                print(json.dumps({"phase": "odd-cut separation", "blossom_cuts": len(blossoms)}), flush=True)
                if not new_cuts:
                    continue
        if not new_cuts:
            if not linear:
                break
            linear = False
            cutoff = upper
            if target_seconds > 0:
                decision_limit = math.floor(upper - target_seconds * 10)
                cutoff = decision_limit
            # At an optimal LP, using an arc at its lower bound incurs its
            # nonnegative reduced cost. Arcs costing more than the entire
            # incumbent gap cannot occur in any better tour.
            reduced_costs = np.asarray(solution.col_dual)
            excluded = np.flatnonzero((values < 1e-8)
                                     & (reduced_costs > cutoff - info.objective_function_value + 1e-3))
            if len(excluded):
                changed = solver.changeColsBounds(len(excluded), excluded.astype(np.int32),
                                                   np.zeros(len(excluded)), np.zeros(len(excluded)))
                if changed != highspy.HighsStatus.kOk:
                    raise RuntimeError("HiGHS rejected reduced-cost arc bounds.")
            if solver.addRow(-np.inf, cutoff, count, np.arange(count, dtype=np.int32),
                             objective) != highspy.HighsStatus.kOk:
                raise RuntimeError("HiGHS rejected incumbent objective cutoff.")
            solver.setOptionValue("solver", "choose")
            solver.setOptionValue("mip_rel_gap", 0)
            solver.setOptionValue("mip_abs_gap", target_seconds * 10 if target_seconds > 0 else 0)
            solver.changeColsIntegrality(count, np.arange(count, dtype=np.int32),
                                         np.full(count, highspy.HighsVarType.kInteger, dtype=np.uint8))
            seed = [size - 1, *best_order, size - 1]
            selected = set(zip(seed, seed[1:]))
            hint = np.asarray([int((int(a), int(b)) in selected) for a, b in zip(rows, cols)], dtype=float)
            if decision_limit is None:
                solver.setSolution(count, np.arange(count, dtype=np.int32), hint)
            continue
        starts, indices = [0], []
        for subset in new_cuts:
            inside = np.zeros(size, dtype=bool)
            inside[list(subset)] = True
            indices.extend(np.flatnonzero(inside[rows] & inside[cols]))
            starts.append(len(indices))
        bounds = np.asarray([len(s) - 1 for s in new_cuts], dtype=float)
        added = solver.addRows(len(new_cuts), np.full(len(new_cuts), -np.inf), bounds,
                               len(indices), np.asarray(starts, dtype=np.int32),
                               np.asarray(indices, dtype=np.int32), np.ones(len(indices)))
        if added != highspy.HighsStatus.kOk:
            raise RuntimeError("HiGHS rejected subtour constraints.")
        cuts.update(new_cuts)
    if checkpoint:
        export_status = solver.writeModel(str(checkpoint))
        if export_status == highspy.HighsStatus.kError or not checkpoint.is_file():
            raise RuntimeError("Could not save incremental optimization model.")
        if export_status == highspy.HighsStatus.kWarning:
            print("HiGHS model export warning: generated names for unnamed rows/columns.", flush=True)
    return {"order_indices": best_order, "lower_bound_seconds": lower_bound / 10,
            "assignment_lower_bound_seconds": assignment_bound / 10,
            "best_driving_seconds": upper / 10, "proven_optimal_for_integer_matrix": upper == lower_bound,
            "gap_percent_of_lower_bound": 100 * (upper / lower_bound - 1),
            "wall_seconds": time.monotonic() - started, "iterations": history, "subtour_cuts": len(cuts)}


def verify_saved_threshold(cost, order, model_path, seconds, target_seconds):
    """Re-solve a saved threshold model before publishing an infeasibility bound."""
    import highspy

    started = time.monotonic()
    size = len(cost)
    rows, cols = np.where(~np.eye(size, dtype=bool))
    objective = cost[rows, cols]
    limit = math.floor(path_cost(cost, order) - target_seconds * 10)
    solver = highspy.Highs()
    solver.setOptionValue("output_flag", False)
    solver.setOptionValue("threads", 2)
    if solver.readModel(str(model_path)) != highspy.HighsStatus.kOk:
        raise RuntimeError("Could not read threshold certificate model.")
    lp = solver.getLp()
    if lp.num_col_ != len(objective) or not np.array_equal(lp.col_cost_, objective):
        raise ValueError("Saved model does not use this directed matrix objective.")
    matrix = csr_matrix((lp.a_matrix_.value_, lp.a_matrix_.index_, lp.a_matrix_.start_),
                        shape=(lp.num_col_, lp.num_row_)).T.tocsr()
    # Later subtour cuts can follow the decision row in the exported model.
    decision_rows = np.flatnonzero(np.asarray(lp.row_upper_) == limit)
    if not any(np.array_equal(matrix.getrow(int(row)).toarray()[0], objective) for row in decision_rows):
        raise ValueError("Saved model has a different decision threshold.")
    solver.setOptionValue("time_limit", seconds)
    solver.run()
    if solver.getModelStatus() != highspy.HighsModelStatus.kInfeasible:
        raise RuntimeError(f"Threshold certificate was not independently confirmed: "
                           f"{solver.modelStatusToString(solver.getModelStatus())}")
    assignment = cost.astype(float)
    np.fill_diagonal(assignment, np.inf)
    a, b = linear_sum_assignment(assignment)
    upper, lower = path_cost(cost, order), limit + 1
    with model_path.open("rb") as file:
        model_hash = hashlib.file_digest(file, "sha256").hexdigest()
    return {"order_indices": list(order), "lower_bound_seconds": lower / 10,
            "assignment_lower_bound_seconds": int(cost[a, b].sum()) / 10,
            "best_driving_seconds": upper / 10, "proven_optimal_for_integer_matrix": upper == lower,
            "gap_percent_of_lower_bound": 100 * (upper / lower - 1),
            "wall_seconds": time.monotonic() - started,
            "threshold_infeasible": True, "decision_limit_seconds": limit / 10,
            "certificate_model_sha256": model_hash}


def solve_bounded(cost, initial_order, seconds, target_seconds=0):
    """DFJ subtour cuts: every relaxation contains every feasible open path."""
    size = len(cost)
    dummy = size - 1
    rows, cols = np.where(~np.eye(size, dtype=bool))
    objective = cost[rows, cols].astype(float)
    arc_index = {(int(a), int(b)): i for i, (a, b) in enumerate(zip(rows, cols))}
    assignment = cost.astype(float)
    np.fill_diagonal(assignment, np.inf)
    a, b = linear_sum_assignment(assignment)
    lower_bound = int(cost[a, b].sum())
    assignment_bound = lower_bound
    best_order = list(initial_order)
    upper_bound = path_cost(cost, best_order)
    cuts, cut_keys, history = [], set(), []
    started = time.monotonic()
    proven = False
    linear_phase = True

    while time.monotonic() - started < seconds and lower_bound < upper_bound:
        rr, cc, vv = [], [], []
        for i, (source, target) in enumerate(zip(rows, cols)):
            rr.extend((int(source), size + int(target)))
            cc.extend((i, i))
            vv.extend((1.0, 1.0))
        low = [1.0] * (2 * size)
        high = low.copy()
        for subset in cuts:
            row = len(low)
            for source in subset:
                for target in subset:
                    if source != target:
                        rr.append(row)
                        cc.append(arc_index[source, target])
                        vv.append(1.0)
            low.append(-np.inf)
            high.append(len(subset) - 1)
        # A known feasible route caps the search without excluding the optimum.
        row = len(low)
        rr.extend([row] * len(objective))
        cc.extend(range(len(objective)))
        vv.extend(objective)
        low.append(-np.inf)
        high.append(upper_bound)
        matrix = coo_matrix((vv, (rr, cc)), shape=(len(low), len(objective))).tocsc()
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0:
            break
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Unrecognized options detected.*threads")
            result = milp(
                objective,
                integrality=np.zeros(len(objective)) if linear_phase else np.ones(len(objective)),
                bounds=Bounds(0, 1),
                constraints=LinearConstraint(matrix, low, high),
                options={"time_limit": min(30, remaining), "mip_rel_gap": 0, "threads": 2},
            )
        if result.status not in (0, 1):
            raise RuntimeError(f"MILP failed: {result.message}")
        dual = getattr(result, "mip_dual_bound", None)
        if linear_phase and result.status == 0:
            dual = result.fun
        if dual is not None and math.isfinite(dual):
            # Integer deciseconds; tolerate floating-point solver noise.
            lower_bound = max(lower_bound, math.ceil(dual - 1e-4))
        entry = {
            "elapsed_seconds": time.monotonic() - started,
            "solver_status": result.message,
            "lower_bound_seconds": lower_bound / 10,
            "incumbent_seconds": upper_bound / 10,
            "subtour_cuts": len(cuts),
            "phase": "fractional-cut LP" if linear_phase else "integer MILP",
        }
        added = 0
        if linear_phase and result.status == 0:
            for subset in fractional_subtours(result.x, rows, cols, size):
                if subset not in cut_keys:
                    cut_keys.add(subset)
                    cuts.append(subset)
                    added += 1
            if added == 0:
                linear_phase = False
        integral = result.x is not None and np.max(np.abs(result.x - np.rint(result.x))) < 1e-6
        if integral:
            selected = np.flatnonzero(result.x > 0.5)
            if len(selected) != size:
                raise RuntimeError("MILP did not select one arc per node.")
            successors = [-1] * size
            for index in selected:
                source, target = int(rows[index]), int(cols[index])
                if successors[source] != -1:
                    raise RuntimeError("MILP selected multiple outgoing arcs.")
                successors[source] = target
            cycles = cycles_from_successors(successors)
            entry["cycle_count"] = len(cycles)
            if len(cycles) == 1:
                order = open_order(successors, dummy)
                value = path_cost(cost, order)
                if abs(value - result.fun) > 0.01:
                    raise RuntimeError("MILP objective disagrees with the reconstructed route.")
                if value < upper_bound:
                    upper_bound, best_order = value, order
                entry["incumbent_seconds"] = upper_bound / 10
            else:
                for cycle in cycles:
                    subset = frozenset(cycle)
                    if subset not in cut_keys:
                        cut_keys.add(subset)
                        cuts.append(subset)
                        added += 1
        if lower_bound > upper_bound:
            raise RuntimeError("Lower bound exceeds the verified feasible route.")
        history.append(entry)
        print(json.dumps(entry), flush=True)
        if lower_bound == upper_bound:
            proven = True
            break
        if target_seconds > 0 and upper_bound - lower_bound < target_seconds * 10:
            break
        if entry["phase"] == "fractional-cut LP":
            continue
        if result.status == 0 and added == 0:
            raise RuntimeError("Optimal relaxation did not yield a route or a new subtour cut.")
    proven = proven or lower_bound == upper_bound
    return {
        "order_indices": best_order,
        "assignment_lower_bound_seconds": assignment_bound / 10,
        "lower_bound_seconds": lower_bound / 10,
        "best_driving_seconds": upper_bound / 10,
        "proven_optimal_for_integer_matrix": proven,
        "gap_percent_of_lower_bound": 100 * (upper_bound - lower_bound) / lower_bound if lower_bound else 0,
        "wall_seconds": time.monotonic() - started,
        "subtour_cuts": len(cuts),
        "iterations": history,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain", default="Kaufland")
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--stores", type=Path, required=True)
    parser.add_argument("--incumbent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--heuristic-seconds", type=int, default=30)
    parser.add_argument("--optimizer-packages", type=Path)
    parser.add_argument("--heuristic-runs", type=int, default=1)
    parser.add_argument("--target-seconds", type=float, default=0)
    parser.add_argument("--previous-result", type=Path)
    parser.add_argument("--solver", choices=["highs", "cp-sat", "incremental", "lkh"], default="highs")
    parser.add_argument("--lkh-runs", type=int, default=10)
    parser.add_argument("--lkh-seed", type=int, default=20261109)
    parser.add_argument("--cp-linearization", type=int, choices=[0, 1, 2], default=2)
    parser.add_argument("--verify-model", type=Path)
    parser.add_argument("--model-output", type=Path)
    parser.add_argument("--post-seconds", type=int, default=0)
    args = parser.parse_args()
    if args.seconds <= 0 or args.heuristic_seconds < 0 or args.heuristic_runs < 1 or args.target_seconds < 0 or args.post_seconds < 0 or args.lkh_runs < 1:
        parser.error("Time limits must be positive (heuristic may be zero).")
    if not 1 <= args.lkh_seed < 2**31:
        parser.error("LKH seed must be a positive signed 32-bit integer.")
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    features = json.loads(args.stores.read_text(encoding="utf-8"))["features"]
    incumbent = json.loads(args.incumbent.read_text(encoding="utf-8"))
    coordinates = [tuple(f["geometry"]["coordinates"]) for f in features]
    signature = hashlib.sha256(json.dumps(coordinates).encode()).hexdigest()
    if signature != matrix["coordinate_sha256"]:
        raise ValueError("Matrix/store coordinate fingerprint mismatch.")
    ids = [f["properties"]["id"] for f in features]
    if len(set(ids)) != len(ids) or set(incumbent["order_ids"]) != set(ids) or len(incumbent["order_ids"]) != len(ids):
        raise ValueError("Incumbent does not visit each unique store exactly once.")
    duration = np.asarray(matrix["durations"], dtype=float)
    n = len(ids)
    if duration.shape != (n, n) or not np.isfinite(duration).all() or (duration < 0).any():
        raise ValueError("Invalid directed duration matrix.")
    cost = np.zeros((n + 1, n + 1), dtype=np.int64)
    cost[:n, :n] = np.rint(duration * 10).astype(np.int64)
    if np.max(np.abs(cost[:n, :n] / 10 - duration)) > 1e-7:
        raise ValueError("Matrix is not already exact to deciseconds; rounding would alter the problem.")
    by_id = {store_id: index for index, store_id in enumerate(ids)}
    original_order = [by_id[store_id] for store_id in incumbent["order_ids"]]
    original_value = path_cost(cost, original_order) / 10
    if abs(original_value - incumbent["driving_seconds"]) > 0.11:
        raise ValueError("Published route duration disagrees with the matrix.")
    print(f"Verified {n} stores; published incumbent {original_value / 3600:.6f} h", flush=True)
    started = time.monotonic()
    improved = original_order
    previous_lower = 0
    if args.previous_result:
        previous = json.loads(args.previous_result.read_text(encoding="utf-8"))
        if previous["matrix_file_sha256"] != hashlib.sha256(args.matrix.read_bytes()).hexdigest():
            raise ValueError("Previous result uses a different matrix.")
        if previous["matrix_coordinate_sha256"] != signature:
            raise ValueError("Previous result uses different coordinates.")
        previous_ids = previous["order_ids"]
        if len(previous_ids) != n or set(previous_ids) != set(ids):
            raise ValueError("Invalid previous route permutation.")
        previous_order = [by_id[i] for i in previous_ids]
        if abs(path_cost(cost, previous_order) / 10 - previous["best_driving_seconds"]) > 1e-6:
            raise ValueError("Previous route objective disagrees with matrix.")
        if path_cost(cost, previous_order) < path_cost(cost, improved):
            improved = previous_order
        previous_lower = round(previous["lower_bound_seconds"] * 10)
        if not (0 <= previous_lower <= path_cost(cost, improved)):
            raise ValueError("Invalid previous lower bound.")
    rng = np.random.default_rng(20261109)
    for run in range(args.heuristic_runs):
        seed = improved
        if run:
            blocks = np.array_split(np.asarray(improved), 6)
            seed = [int(i) for block in rng.permutation(6) for i in blocks[block]]
        candidate = improve_route(cost, seed, args.heuristic_seconds, args.optimizer_packages)
        if path_cost(cost, candidate) < path_cost(cost, improved):
            improved = candidate
        print(f"Heuristic run {run + 1}: best {path_cost(cost, improved) / 36000:.6f} h", flush=True)
    print(f"Heuristic incumbent {path_cost(cost, improved) / 36000:.6f} h", flush=True)
    if args.verify_model:
        if args.target_seconds <= 0 or not args.previous_result:
            parser.error("--verify-model requires a previous result and a positive target.")
        result = verify_saved_threshold(cost, improved, args.verify_model, args.seconds, args.target_seconds)
    elif args.solver == "cp-sat":
        result = solve_circuit(cost, improved, args.seconds, previous_lower,
                               args.optimizer_packages, args.target_seconds, args.cp_linearization)
    elif args.solver == "lkh":
        result = solve_lkh(cost, improved, args.seconds, previous_lower, args.lkh_runs, args.lkh_seed)
    elif args.solver == "incremental":
        result = solve_incremental(cost, improved, args.seconds, previous_lower, args.target_seconds,
                                   args.model_output)
    else:
        result = solve_bounded(cost, improved, args.seconds, args.target_seconds)
    order = result.pop("order_indices")
    if args.post_seconds:
        order = improve_route(cost, order, args.post_seconds, args.optimizer_packages)
        result["best_driving_seconds"] = path_cost(cost, order) / 10
        result["gap_percent_of_lower_bound"] = 100 * (result["best_driving_seconds"] / result["lower_bound_seconds"] - 1)
        result["proven_optimal_for_integer_matrix"] = result["best_driving_seconds"] == result["lower_bound_seconds"]
    if sorted(order) != list(range(n)):
        raise RuntimeError("Final route validation failed.")
    result.update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "chain": args.chain,
        "store_count": n,
        "objective": "Minimum directed modeled driving time; free start, free finish, no return",
        "matrix_coordinate_sha256": signature,
        "matrix_file_sha256": hashlib.sha256(args.matrix.read_bytes()).hexdigest(),
        "road_data_timestamp": incumbent["metadata"]["road_data_timestamp"],
        "solver": ("HiGHS independently rechecked threshold-infeasibility model with subtour and odd cuts"
                   if args.verify_model else
                   "OR-Tools CP-SAT directed circuit with verified incumbent and prior lower bound"
                   if args.solver == "cp-sat" else
                   "LKH directed-tour search; lower bound retained from previous verified solver run"
                   if args.solver == "lkh" else
                   "Incremental HiGHS LP min-cut and odd-cut separation with retained basis, followed by DFJ MILP"
                   if args.solver == "incremental" else
                   "SciPy/HiGHS fractional min-cut LP separation followed by directed DFJ MILP"),
        "original_driving_seconds": original_value,
        "published_gap_percent_of_lower_bound": 100 * (original_value / result["lower_bound_seconds"] - 1),
        "improvement_seconds": original_value - result["best_driving_seconds"],
        "order_ids": [ids[i] for i in order],
        "distance_metres": sum(matrix["distances"][a][b] for a, b in zip(order, order[1:])),
        "total_wall_seconds": time.monotonic() - started,
        "exact_solver_budget_seconds": args.seconds,
        "heuristic_budget_seconds": args.heuristic_seconds * args.heuristic_runs + args.post_seconds,
        "heuristic_runs": args.heuristic_runs,
        "target_saving_seconds": args.target_seconds,
        "limitations": [
            "Bounds apply only to this static OSRM matrix, not live road conditions.",
            f"All {n} mapped stores retained; no opening hours, shopping, rest or first-store travel.",
            "Bounds are numerical solver results for the integer-decisecond matrix, not a formal exact-arithmetic proof.",
            "No route geometry was regenerated and no website route was changed.",
        ],
    })
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("RESULT", json.dumps({k: v for k, v in result.items() if k not in ("iterations", "order_ids")}), flush=True)


if __name__ == "__main__":
    main()
