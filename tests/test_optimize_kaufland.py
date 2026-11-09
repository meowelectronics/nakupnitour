import itertools
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from optimize_kaufland import blossom_inequalities, cycles_from_successors, fractional_subtours, open_order, patch_cycle_cover, path_cost, solve_bounded, solve_circuit, solve_incremental, solve_lkh, verify_saved_threshold


class OptimalityTests(unittest.TestCase):
    def test_threshold_certificate_never_excludes_a_better_tour(self):
        cost = np.zeros((8, 8), dtype=np.int64)
        cost[:7, :7] = np.random.default_rng(0).integers(1, 200, (7, 7))
        np.fill_diagonal(cost, 0)
        exact_order = min(itertools.permutations(range(7)), key=lambda p: path_cost(cost, p))
        optimum = path_cost(cost, exact_order) / 10
        result = solve_incremental(cost, list(exact_order), 10, 0, target_seconds=.1)
        self.assertLessEqual(result["lower_bound_seconds"], optimum)
        self.assertLess(result["best_driving_seconds"] - result["lower_bound_seconds"], .1 + 1e-8)
        with tempfile.TemporaryDirectory() as work:
            model = Path(work) / "threshold.mps"
            solve_incremental(cost, list(exact_order), 10, 0, target_seconds=.1, checkpoint=model)
            verified = verify_saved_threshold(cost, list(exact_order), model, 10, .1)
            self.assertTrue(verified["threshold_infeasible"])
            self.assertLessEqual(verified["lower_bound_seconds"], optimum)
            self.assertEqual(len(verified["certificate_model_sha256"]), 64)

    def test_blossoms_separate_fractional_prism_but_preserve_every_tour(self):
        size = 6
        rows, cols = np.where(~np.eye(size, dtype=bool))
        y = np.zeros((size, size))
        for a, b in [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)]:
            y[a, b] = y[b, a] = .5
        for a, b in [(0, 3), (1, 4), (2, 5)]:
            y[a, b] = y[b, a] = 1
        values = y[rows, cols] / 2
        inequalities = blossom_inequalities(values, rows, cols, size)
        self.assertTrue(inequalities)
        for indices, coefficients, lower in inequalities:
            self.assertLess(float(values[indices] @ coefficients), lower)
            for tour in itertools.permutations(range(1, size)):
                order = [0, *tour, 0]
                edges = set(zip(order, order[1:]))
                x = np.asarray([int((int(a), int(b)) in edges) for a, b in zip(rows, cols)])
                self.assertGreaterEqual(float(x[indices] @ coefficients), lower)

    def test_lkh_preserves_directed_open_path_and_verified_bound(self):
        cost = np.zeros((8, 8), dtype=np.int64)
        cost[:7, :7] = np.random.default_rng(42).integers(1, 200, (7, 7))
        np.fill_diagonal(cost, 0)
        expected = min(path_cost(cost, p) for p in itertools.permutations(range(7)))
        result = solve_lkh(cost, list(range(7)), 10, expected - 1, 2)
        self.assertEqual(sorted(result["order_indices"]), list(range(7)))
        self.assertGreaterEqual(result["best_driving_seconds"], expected / 10)
        self.assertLessEqual(result["best_driving_seconds"], path_cost(cost, list(range(7))) / 10)
        self.assertLessEqual(result["lower_bound_seconds"], expected / 10)

    def test_patching_asymmetric_cycles_returns_complete_open_path(self):
        rng = np.random.default_rng(42)
        cost = rng.integers(1, 200, (7, 7))
        cost[6, :] = cost[:, 6] = 0
        order = patch_cycle_cover(cost, [1, 0, 3, 2, 5, 6, 4])
        self.assertEqual(sorted(order), list(range(6)))
        self.assertGreater(path_cost(cost, order), 0)

    def test_incremental_target_is_an_absolute_time_gap(self):
        cost = np.zeros((7, 7), dtype=np.int64)
        cost[:6, :6] = 100
        np.fill_diagonal(cost, 0)
        for a, b in [(0, 1), (2, 3), (4, 5)]:
            cost[a, b] = cost[b, a] = 1
        result = solve_incremental(cost, list(range(6)), 10, 0, target_seconds=20)
        self.assertLess(result["best_driving_seconds"] - result["lower_bound_seconds"], 20)
        self.assertFalse(result["proven_optimal_for_integer_matrix"])

    def test_incremental_solver_matches_exhaustive_search(self):
        for seed in range(5):
            cost = np.zeros((8, 8), dtype=np.int64)
            cost[:7, :7] = np.random.default_rng(seed).integers(1, 200, (7, 7))
            np.fill_diagonal(cost, 0)
            expected = min(path_cost(cost, p) for p in itertools.permutations(range(7)))
            result = solve_incremental(cost, list(range(7)), 10, 0)
            self.assertEqual(result["lower_bound_seconds"], expected / 10)
            self.assertEqual(result["best_driving_seconds"], expected / 10)
            self.assertEqual(sorted(result["order_indices"]), list(range(7)))

    @unittest.skipUnless(os.environ.get("OPTIMIZER_PACKAGES"), "Set OPTIMIZER_PACKAGES for optional CP-SAT tests.")
    def test_circuit_solver_matches_exhaustive_search(self):
        for seed in range(3):
            cost = np.zeros((7, 7), dtype=np.int64)
            cost[:6, :6] = np.random.default_rng(seed).integers(1, 200, (6, 6))
            np.fill_diagonal(cost, 0)
            expected = min(path_cost(cost, p) for p in itertools.permutations(range(6)))
            result = solve_circuit(cost, list(range(6)), 10, expected - 1,
                                   os.environ["OPTIMIZER_PACKAGES"])
            self.assertEqual(result["lower_bound_seconds"], expected / 10)
            self.assertEqual(result["best_driving_seconds"], expected / 10)
            self.assertEqual(sorted(result["order_indices"]), list(range(6)))

    def test_fractional_min_cut_separates_disconnected_components(self):
        rows, cols = np.where(~np.eye(6, dtype=bool))
        values = np.where((rows < 3) == (cols < 3), .5, 0)
        cuts = fractional_subtours(values, rows, cols, 6)
        self.assertTrue(cuts)
        for subset in cuts:
            internal = np.isin(rows, list(subset)) & np.isin(cols, list(subset))
            self.assertGreater(values[internal].sum(), len(subset) - 1)

    def test_fractional_min_cut_does_not_cut_feasible_tour_mixtures(self):
        size = 7
        rows, cols = np.where(~np.eye(size, dtype=bool))
        values = np.zeros(len(rows))
        for shift in [1, 2, 3]:
            values[(cols - rows) % size == shift] = 1 / 3
        self.assertEqual(fractional_subtours(values, rows, cols, size), set())

    def test_disconnected_cycles_are_not_routes(self):
        self.assertEqual(cycles_from_successors([1, 0, 3, 2]), [[0, 1], [2, 3]])
        with self.assertRaises(ValueError):
            open_order([1, 0, 3, 2], 3)
        with self.assertRaises(ValueError):
            cycles_from_successors([1, 1])

    def test_asymmetric_open_path_matches_exhaustive_search(self):
        for seed in range(3):
            rng = np.random.default_rng(seed)
            cost = np.zeros((6, 6), dtype=np.int64)
            cost[:5, :5] = rng.integers(10, 200, size=(5, 5))
            np.fill_diagonal(cost, 0)
            expected = min(path_cost(cost, p) for p in itertools.permutations(range(5)))
            result = solve_bounded(cost, list(range(5)), 10)
            self.assertTrue(result["proven_optimal_for_integer_matrix"])
            self.assertEqual(result["best_driving_seconds"], expected / 10)
            self.assertEqual(result["lower_bound_seconds"], expected / 10)
            self.assertEqual(sorted(result["order_indices"]), list(range(5)))

    def test_subtour_cuts_close_disconnected_assignment_gap(self):
        cost = np.zeros((7, 7), dtype=np.int64)
        cost[:6, :6] = 100
        np.fill_diagonal(cost, 0)
        for a, b in [(0, 1), (2, 3), (4, 5)]:
            cost[a, b] = cost[b, a] = 1
        expected = min(path_cost(cost, p) for p in itertools.permutations(range(6)))
        result = solve_bounded(cost, list(range(6)), 10)
        self.assertGreater(result["subtour_cuts"], 0)
        self.assertLess(result["assignment_lower_bound_seconds"], expected / 10)
        self.assertTrue(result["proven_optimal_for_integer_matrix"])
        self.assertEqual(result["best_driving_seconds"], expected / 10)


if __name__ == "__main__":
    unittest.main()
