"""Routing against the REAL capability registry and routing policy files."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from agentctl.errors import HumanGate, PlanningError
from agentctl.routing import (
    ROUTE_STATUSES,
    reviewer_class_for,
    route_capability,
    route_reviewer,
    route_task,
)

from .helpers_lane_a import SOURCE_ROOT, LaneARepo, install_fake_claude

REGISTRY = json.loads((SOURCE_ROOT / "agent-system/routing/capability-registry.json").read_text())
BY_CLASS = {entry["capability_class"]: entry for entry in REGISTRY["entries"]}


class RoutingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.bin = tempfile.TemporaryDirectory()
        self.addCleanup(self.bin.cleanup)
        self.fake = install_fake_claude(pathlib.Path(self.bin.name))

    def with_claude(self):
        return mock.patch.dict(os.environ, {"PATH": f"{self.bin.name}{os.pathsep}{os.environ['PATH']}"})

    def without_claude(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        return mock.patch.dict(os.environ, {"PATH": empty})


class RegistryRoutingTests(RoutingTestCase):
    def test_anthropic_classes_route_to_the_cli_with_the_registry_model_verbatim(self) -> None:
        """Protects: model ids come only from the registry file, and an installed CLI makes the class available."""
        with self.with_claude():
            for name, entry in BY_CLASS.items():
                if entry["provider"] != "anthropic":
                    continue
                with self.subTest(capability_class=name):
                    decision = route_capability(name, root=SOURCE_ROOT)
                    self.assertTrue(decision.available)
                    self.assertEqual(decision.status, "available")
                    self.assertEqual(decision.model_id, entry["model_id"])
                    self.assertEqual(decision.model_version, entry["model_version"])
                    self.assertEqual(decision.binding_type, entry["binding_type"])
                    self.assertEqual(decision.adapter, "claude_cli")
                    self.assertEqual(decision.executable, str(self.fake.executable))

    def test_anthropic_class_without_the_cli_is_executable_missing(self) -> None:
        """Protects: a missing CLI is explicit and actionable, and nothing else is substituted."""
        with self.without_claude():
            decision = route_capability("standard_implementation", root=SOURCE_ROOT)
        self.assertEqual(decision.status, "executable_missing")
        self.assertFalse(decision.available)
        self.assertIn("PATH", decision.message)

    def test_openai_and_google_classes_are_unsupported_even_when_claude_is_installed(self) -> None:
        """Protects: no cross-provider substitution; each unsupported binding names its provider and model."""
        with self.with_claude():
            for name, entry in BY_CLASS.items():
                if entry["provider"] == "anthropic":
                    continue
                with self.subTest(capability_class=name, provider=entry["provider"]):
                    decision = route_capability(name, root=SOURCE_ROOT)
                    self.assertEqual(decision.status, "unsupported_provider")
                    self.assertIsNone(decision.adapter)
                    self.assertEqual((decision.provider, decision.model_id), (entry["provider"], entry["model_id"]))
                    self.assertIn(entry["provider"], decision.message)
                    self.assertIn("No other provider or model is substituted", decision.message)
        self.assertIn("unsupported_provider", ROUTE_STATUSES)

    def test_auth_probe_reports_a_logged_out_cli_as_unauthenticated(self) -> None:
        """Protects: an unauthenticated CLI blocks routing when auth is checked (and does not when it is logged in)."""
        with self.with_claude():
            self.fake.set(logged_in=False)
            self.assertEqual(
                route_capability("standard_implementation", root=SOURCE_ROOT, check_auth=True).status,
                "unauthenticated",
            )
            self.fake.set(logged_in=True)
            self.assertTrue(route_capability("standard_implementation", root=SOURCE_ROOT, check_auth=True).available)

    def test_changing_a_provider_changes_only_the_registry_file(self) -> None:
        """Protects: the registry is the single binding point; editing it re-routes with no code change."""
        with tempfile.TemporaryDirectory() as root:
            routing = pathlib.Path(root) / "agent-system/routing"
            shutil.copytree(SOURCE_ROOT / "agent-system/routing", routing)
            registry = json.loads((routing / "capability-registry.json").read_text())
            for entry in registry["entries"]:
                if entry["capability_class"] == "complex_implementation":
                    entry.update(provider="anthropic", model_id="claude-opus-5-5")
            (routing / "capability-registry.json").write_text(json.dumps(registry))
            with self.with_claude():
                decision = route_capability("complex_implementation", root=root)
        self.assertEqual((decision.status, decision.model_id), ("available", "claude-opus-5-5"))

    def test_unknown_capability_class_is_a_planning_error(self) -> None:
        """Protects: routing never invents a binding for a class the registry does not list."""
        with self.assertRaises(PlanningError):
            route_capability("no_such_class", root=SOURCE_ROOT)


class ReviewerSelectionTests(RoutingTestCase):
    def test_reviewer_matrix_resolves_names_and_prose_deterministically(self) -> None:
        """Protects: reviewer class = matrix value; prose uses the earliest registered class named in it."""
        expected = {
            "mechanical_execution": "standard_implementation",
            "standard_implementation": "complex_implementation",
            "complex_implementation": "complex_implementation",  # prose: first listed class
            "work_package_planning": "complex_implementation",  # prose: after 'schema validation plus'
        }
        for task_class, reviewer in expected.items():
            with self.subTest(task_class=task_class):
                self.assertEqual(reviewer_class_for(task_class, root=SOURCE_ROOT), reviewer)

    def test_architectural_authority_has_no_machine_reviewer(self) -> None:
        """Protects: a class whose policy demands human approval is never given a model reviewer."""
        with self.assertRaises(HumanGate):
            reviewer_class_for("architectural_authority", root=SOURCE_ROOT)

    def test_high_risk_raises_the_reviewer_floor(self) -> None:
        """Protects: risk_may_raise_reviewer_floor; a high-risk mechanical task is not reviewed by a standard model."""
        self.assertEqual(reviewer_class_for("mechanical_execution", risk="low", root=SOURCE_ROOT), "standard_implementation")
        self.assertEqual(reviewer_class_for("mechanical_execution", risk="high", root=SOURCE_ROOT), "complex_implementation")

    def test_real_registry_reviewer_for_a_standard_task_is_unsupported(self) -> None:
        """Protects: with the real registry the required reviewer (complex_implementation -> openai) is unsupported, not substituted."""
        with self.with_claude():
            decision = route_reviewer("standard_implementation", root=SOURCE_ROOT)
        self.assertEqual((decision.capability_class, decision.status), ("complex_implementation", "unsupported_provider"))


class TaskRouteTests(RoutingTestCase):
    def test_real_registry_blocks_a_standard_task_with_reviewer_unavailable(self) -> None:
        """Protects: the worker is routable but the task is not, because its required reviewer is unsupported."""
        repo = LaneARepo(self)
        with self.with_claude():
            route = route_task(repo.TASK_ID, store=repo.store)
        self.assertTrue(route.worker.available)
        self.assertEqual(route.status, "reviewer_unavailable")
        self.assertFalse(route.available)
        self.assertIn("openai", route.message)

    def test_registry_fixture_binding_both_classes_to_anthropic_is_runnable(self) -> None:
        """Protects: the same task routes when both worker and reviewer classes have an adapter (temp registry)."""
        repo = LaneARepo(
            self,
            registry={
                "standard_implementation": ("anthropic", "claude-sonnet-5"),
                "complex_implementation": ("anthropic", "claude-sonnet-5"),
            },
        )
        with self.with_claude():
            route = route_task(repo.TASK_ID, store=repo.store)
        self.assertTrue(route.available)
        self.assertEqual(route.reviewer.capability_class, "complex_implementation")


if __name__ == "__main__":
    unittest.main()
