"""Deterministic dependency DAG and invalidation tests."""

from __future__ import annotations

import unittest

from agentctl.dag import CycleDetected, DependencyGraph


class DependencyGraphTests(unittest.TestCase):
    def test_topological_order_is_dependency_first_and_deterministic(self) -> None:
        graph = DependencyGraph(
            {
                "package": {"compile", "resources"},
                "compile": {"parse"},
                "resources": set(),
                "parse": set(),
                "independent": set(),
            }
        )
        order = graph.topological_order()
        positions = {node: index for index, node in enumerate(order)}
        self.assertLess(positions["parse"], positions["compile"])
        self.assertLess(positions["compile"], positions["package"])
        self.assertLess(positions["resources"], positions["package"])
        self.assertEqual(order, graph.topological_order())

    def test_cycle_detection_returns_only_the_offending_cycle_nodes(self) -> None:
        graph = DependencyGraph(
            {
                "A": {"C"},
                "B": {"A"},
                "C": {"B"},
                "downstream": {"C"},
                "unrelated": set(),
            }
        )
        self.assertEqual(graph.find_cycle(), ["A", "C", "B"])
        with self.assertRaises(CycleDetected) as caught:
            graph.topological_order()
        self.assertEqual(list(caught.exception.nodes), ["A", "C", "B"])
        self.assertNotIn("downstream", caught.exception.nodes)

    def test_descendants_cover_the_full_transitive_closure(self) -> None:
        graph = DependencyGraph(
            {
                "B": {"A"},
                "C": {"B"},
                "D": {"A"},
                "E": {"D"},
                "unrelated": set(),
            }
        )
        self.assertEqual(graph.descendants("A"), {"B", "C", "D", "E"})

    def test_interface_hash_change_invalidates_consumers_and_all_descendants(self) -> None:
        graph = DependencyGraph(
            {
                "consumer": set(),
                "child": {"consumer"},
                "grandchild": {"child"},
                "same_hash": set(),
                "unrelated": set(),
            }
        )
        pins = {
            "consumer": {"contract:core-domain-v1": "old"},
            "same_hash": {"contract:core-domain-v1": "new"},
            "unrelated": {"contract:other-v1": "old"},
        }
        stale = graph.stale_for_interface_change(
            "contract:core-domain-v1", "new", pins
        )
        self.assertEqual(stale, {"consumer", "child", "grandchild"})

    def test_unknown_dependency_is_still_a_graph_node(self) -> None:
        graph = DependencyGraph({"B": {"A"}})
        self.assertEqual(graph.topological_order(), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
