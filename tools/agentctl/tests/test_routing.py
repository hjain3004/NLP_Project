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
    ALLOW_CANDIDATE_ENV,
    ROUTE_STATUSES,
    load_candidates,
    load_registry,
    reviewer_class_for,
    route_capability,
    route_reviewer,
    route_task,
)

from .helpers_lane_a import SOURCE_ROOT, LaneARepo, git, install_fake_claude

REGISTRY = json.loads((SOURCE_ROOT / "agent-system/routing/capability-registry.json").read_text())
CHAMPIONS = [entry for entry in REGISTRY["entries"] if entry["binding_type"] != "candidate"]
BY_CLASS = {entry["capability_class"]: entry for entry in CHAMPIONS}
BASE_COMMIT = "81063b15f3249a2fbda7b1077a6d5112a02d646d"


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


class CandidateBindingTests(RoutingTestCase):
    """Against the REAL registry: a disclosed, unevaluated candidate is usable only by explicit opt-in."""

    def test_registry_discloses_one_unevaluated_candidate_and_leaves_the_champions_unchanged(self) -> None:
        """Protects governance: champions are byte-for-byte what the base commit had; the new binding is labelled and cannot pass for a champion."""
        base = json.loads(git(SOURCE_ROOT, "show", f"{BASE_COMMIT}:agent-system/routing/capability-registry.json"))
        self.assertEqual(CHAMPIONS, base["entries"])
        candidates = [e for e in REGISTRY["entries"] if e["binding_type"] == "candidate"]
        self.assertEqual(len(candidates), 1)
        (candidate,) = candidates
        self.assertEqual(
            (candidate["capability_class"], candidate["provider"], candidate["model_id"],
             candidate["model_version"], candidate["candidate_status"]),
            ("complex_implementation", "anthropic", "claude-opus-5-5", "unpinned", "unevaluated"),
        )
        self.assertIn("champion-challenger-v1", json.dumps(candidate["promotion_requires"]))
        self.assertTrue(candidate["scope"])

    def test_no_model_name_is_hard_coded_in_runtime_code(self) -> None:
        """Protects: models live only in the registry file (AGENTS.md section 2); routing never names one."""
        package = SOURCE_ROOT / "tools/agentctl/agentctl"
        names = {e["model_id"] for e in REGISTRY["entries"]}
        for path in package.rglob("*.py"):
            text = path.read_text()
            for name in names:
                with self.subTest(file=path.name, model=name):
                    self.assertNotIn(name, text)

    def test_without_opt_in_the_champion_refusal_names_the_candidate_and_the_exact_flag(self) -> None:
        """Protects: never a silent substitution; the refusal tells the operator precisely what opting in means."""
        with self.with_claude():
            decision = route_reviewer("standard_implementation", root=SOURCE_ROOT)
        self.assertEqual((decision.provider, decision.status, decision.binding_type),
                         ("openai", "unsupported_provider", "initial_prior"))
        self.assertIn("claude-opus-5-5", decision.message)
        self.assertIn("--allow-candidate-bindings", decision.message)
        self.assertIn(ALLOW_CANDIDATE_ENV, decision.message)
        self.assertIn("owner decision", decision.message)
        self.assertIn("champion-challenger-v1", decision.message)

    def test_with_opt_in_the_candidate_is_selected_and_says_why_the_champion_was_not(self) -> None:
        """Protects: the decision itself carries binding_type candidate and the champion's unavailability reason."""
        with self.with_claude():
            decision = route_reviewer("standard_implementation", risk="low", root=SOURCE_ROOT, allow_candidate_bindings=True)
        self.assertTrue(decision.available)
        self.assertEqual(
            (decision.capability_class, decision.provider, decision.model_id, decision.binding_type),
            ("complex_implementation", "anthropic", "claude-opus-5-5", "candidate"),
        )
        self.assertIn("openai/gpt-5.6-terra", decision.champion_unavailable_reason)
        self.assertIn("unsupported_provider", decision.champion_unavailable_reason)
        self.assertIn("unevaluated candidate", decision.message)
        self.assertEqual(decision.to_payload()["binding_type"], "candidate")

    def test_environment_opt_in_equals_the_flag_but_only_for_the_value_one(self) -> None:
        """Protects: an ambient variable opts in only when it is exactly '1'; look-alike values do not."""
        with self.with_claude():
            for value, expected in (("1", "candidate"), ("0", "initial_prior"), ("true", "initial_prior"), ("", "initial_prior")):
                with self.subTest(value=value), mock.patch.dict(os.environ, {ALLOW_CANDIDATE_ENV: value}):
                    self.assertEqual(
                        route_reviewer("standard_implementation", risk="low", root=SOURCE_ROOT).binding_type, expected)

    def test_champion_always_wins_when_it_is_runnable(self) -> None:
        """Protects: champion first. Opt-in never swaps in a candidate while the champion has an adapter."""
        with tempfile.TemporaryDirectory() as root:
            routing = pathlib.Path(root) / "agent-system/routing"
            shutil.copytree(SOURCE_ROOT / "agent-system/routing", routing)
            registry = json.loads((routing / "capability-registry.json").read_text())
            for entry in registry["entries"]:
                if entry["capability_class"] == "complex_implementation" and entry["binding_type"] != "candidate":
                    entry.update(provider="anthropic", model_id="claude-sonnet-5")
            (routing / "capability-registry.json").write_text(json.dumps(registry))
            with self.with_claude():
                decision = route_capability("complex_implementation", root=root, allow_candidate_bindings=True)
        self.assertEqual((decision.model_id, decision.binding_type), ("claude-sonnet-5", "initial_prior"))
        self.assertIsNone(decision.champion_unavailable_reason)

    def test_classes_without_a_candidate_stay_unsupported_even_with_opt_in(self) -> None:
        """Protects: opt-in is not a general fallback; openai/google classes with no candidate are still reported honestly."""
        with self.with_claude():
            for name in ("architectural_authority", "mechanical_execution"):
                with self.subTest(capability_class=name):
                    decision = route_capability(name, root=SOURCE_ROOT, allow_candidate_bindings=True)
                    self.assertEqual(decision.status, "unsupported_provider")
                    self.assertEqual(decision.binding_type, "initial_prior")
                    self.assertIn("No other provider or model is substituted", decision.message)

    def test_a_candidate_whose_cli_is_missing_is_reported_not_papered_over(self) -> None:
        """Protects: with opt-in but no executable the result is a candidate-labelled executable_missing carrying the champion's reason."""
        with self.without_claude():
            decision = route_reviewer("standard_implementation", risk="low", root=SOURCE_ROOT, allow_candidate_bindings=True)
        self.assertEqual((decision.status, decision.binding_type), ("executable_missing", "candidate"))
        self.assertFalse(decision.available)
        self.assertIn("openai/gpt-5.6-terra", decision.champion_unavailable_reason)

    def test_two_candidates_for_one_class_are_ambiguous_and_refused(self) -> None:
        """Protects: routing never picks between candidates on its own."""
        with tempfile.TemporaryDirectory() as root:
            routing = pathlib.Path(root) / "agent-system/routing"
            shutil.copytree(SOURCE_ROOT / "agent-system/routing", routing)
            registry = json.loads((routing / "capability-registry.json").read_text())
            extra = dict(next(e for e in registry["entries"] if e["binding_type"] == "candidate"))
            extra["model_id"] = "claude-other-candidate"
            registry["entries"].append(extra)
            (routing / "capability-registry.json").write_text(json.dumps(registry))
            with self.assertRaises(PlanningError):
                route_capability("complex_implementation", root=root, allow_candidate_bindings=True)

    def test_real_registry_task_route_needs_the_opt_in_and_then_pairs_champion_author_with_candidate_reviewer(self) -> None:
        """Protects the end state of Task 3: standard task = claude-sonnet-5 (champion) author + claude-opus-5-5 (candidate) reviewer, only on opt-in."""
        repo = LaneARepo(self)
        with self.with_claude():
            refused = route_task(repo.TASK_ID, store=repo.store)
            allowed = route_task(repo.TASK_ID, store=repo.store, allow_candidate_bindings=True)
        self.assertEqual(refused.status, "reviewer_unavailable")
        self.assertIn("--allow-candidate-bindings", refused.message)
        self.assertTrue(allowed.available)
        self.assertEqual((allowed.worker.model_id, allowed.worker.binding_type), ("claude-sonnet-5", "initial_prior"))
        self.assertEqual((allowed.reviewer.model_id, allowed.reviewer.binding_type), ("claude-opus-5-5", "candidate"))
        self.assertEqual(allowed.candidate_roles, ("reviewer",))
        self.assertEqual(refused.candidate_roles, ())
        self.assertEqual(allowed.to_payload()["candidate_roles"], ["reviewer"])


def registry_fixture(test: unittest.TestCase, *, candidate: dict | None = None, policy: dict | None = None,
                     entries=None) -> pathlib.Path:
    """A temporary repository root holding copies of the routing files, optionally edited."""
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    routing = pathlib.Path(directory.name) / "agent-system/routing"
    shutil.copytree(SOURCE_ROOT / "agent-system/routing", routing)
    registry = json.loads((routing / "capability-registry.json").read_text())
    if candidate is not None:
        for index, entry in enumerate(registry["entries"]):
            if entry["binding_type"] == "candidate":
                registry["entries"][index] = candidate
    if entries is not None:
        registry["entries"] = entries(registry["entries"])
    (routing / "capability-registry.json").write_text(json.dumps(registry))
    if policy is not None:
        (routing / "champion-challenger.json").write_text(json.dumps(policy))
    return pathlib.Path(directory.name)


class CandidateSampleScopeTests(RoutingTestCase):
    """champion-challenger-v1 evaluates a challenger on `sample_scope` risks only; routing never goes beyond it."""

    SCOPE = json.loads((SOURCE_ROOT / "agent-system/routing/champion-challenger.json").read_text())["sample_scope"]

    def test_the_policy_scope_is_low_and_medium(self) -> None:
        """Protects the premise of the tests below: the real policy file names exactly these risks."""
        self.assertEqual(self.SCOPE, ["low", "medium"])

    def test_a_critical_task_is_refused_the_candidate_even_with_opt_in(self) -> None:
        """Protects champion-challenger-v1: a critical-risk task never gets an unevaluated candidate reviewer, flag or not."""
        with self.with_claude():
            decision = route_reviewer("standard_implementation", risk="critical", root=SOURCE_ROOT,
                                      allow_candidate_bindings=True)
        self.assertFalse(decision.available)
        self.assertEqual((decision.status, decision.binding_type, decision.provider),
                         ("unsupported_provider", "initial_prior", "openai"))
        for needle in ("critical", "sample_scope", "low", "medium", "champion-challenger-v1", "claude-opus-5-5"):
            self.assertIn(needle, decision.message)
        self.assertIn("promot", decision.message)  # actionable: what would make the candidate usable

    def test_high_risk_is_outside_the_scope_for_workers_too(self) -> None:
        """Protects: the same rule applies to an author binding, not only to a reviewer."""
        with self.with_claude():
            decision = route_capability("complex_implementation", risk="high", root=SOURCE_ROOT,
                                        allow_candidate_bindings=True)
        self.assertEqual((decision.available, decision.binding_type), (False, "initial_prior"))
        self.assertIn("sample_scope", decision.message)

    def test_in_scope_risks_still_route_the_candidate(self) -> None:
        """Protects: the refusal is specific to risks outside the scope; low and medium keep working."""
        with self.with_claude():
            for risk in self.SCOPE:
                with self.subTest(risk=risk):
                    decision = route_reviewer("standard_implementation", risk=risk, root=SOURCE_ROOT,
                                              allow_candidate_bindings=True)
                    self.assertEqual((decision.available, decision.binding_type), (True, "candidate"))

    def test_an_unknown_task_risk_fails_closed(self) -> None:
        """Protects: a caller that cannot say the task's risk cannot use a candidate (absence is not 'in scope')."""
        with self.with_claude():
            decision = route_reviewer("standard_implementation", root=SOURCE_ROOT, allow_candidate_bindings=True)
        self.assertEqual((decision.available, decision.binding_type), (False, "initial_prior"))
        self.assertIn("risk", decision.message)

    def test_the_scope_comes_from_the_policy_file_not_from_code(self) -> None:
        """Protects: editing champion-challenger.json (the policy owner's file) changes what routing allows, with no code change."""
        policy = json.loads((SOURCE_ROOT / "agent-system/routing/champion-challenger.json").read_text())
        narrowed = registry_fixture(self, policy={**policy, "sample_scope": ["low"]})
        widened = registry_fixture(self, policy={**policy, "sample_scope": ["low", "medium", "high"]})
        with self.with_claude():
            def reviewer(root, risk):
                return route_reviewer("standard_implementation", risk=risk, root=root, allow_candidate_bindings=True)
            self.assertFalse(reviewer(narrowed, "medium").available)
            self.assertTrue(reviewer(narrowed, "low").available)
            self.assertTrue(reviewer(widened, "high").available)
            self.assertFalse(reviewer(widened, "critical").available)

    def test_an_unreadable_or_malformed_scope_is_a_planning_error_not_a_default(self) -> None:
        """Protects: a policy file without a usable sample_scope is not silently treated as 'everything' or 'nothing'."""
        policy = json.loads((SOURCE_ROOT / "agent-system/routing/champion-challenger.json").read_text())
        for label, edit in (("missing", lambda p: p.pop("sample_scope")), ("not a list", lambda p: p.update(sample_scope="low")),
                            ("empty", lambda p: p.update(sample_scope=[])), ("not strings", lambda p: p.update(sample_scope=[1]))):
            broken = dict(policy)
            edit(broken)
            root = registry_fixture(self, policy=broken)
            with self.subTest(case=label), self.with_claude(), self.assertRaises(PlanningError):
                route_reviewer("standard_implementation", risk="low", root=root, allow_candidate_bindings=True)

    def test_without_opt_in_the_scope_is_not_consulted(self) -> None:
        """Protects: the default refusal is unchanged (it names the flag); the scope only narrows what the flag can do."""
        with self.with_claude():
            decision = route_reviewer("standard_implementation", risk="critical", root=SOURCE_ROOT)
        self.assertIn("--allow-candidate-bindings", decision.message)


class MalformedRegistryEntryTests(RoutingTestCase):
    """A corrupt registry entry is a typed PlanningError that names the entry, never a KeyError, opt-in or not."""

    CANDIDATE = next(e for e in REGISTRY["entries"] if e["binding_type"] == "candidate")

    def broken(self, **changes):
        entry = dict(self.CANDIDATE)
        for key, value in changes.items():
            if value is None:
                entry.pop(key)
            else:
                entry[key] = value
        return registry_fixture(self, candidate=entry)

    def test_each_missing_or_empty_required_field_is_named(self) -> None:
        """Protects: missing model_id/provider/model_version (or blank/non-string values) fail in the loader with the entry identified."""
        cases = (
            ("model_id", None), ("provider", None), ("model_version", None), ("capability_class", None),
            ("model_id", ""), ("provider", "   "), ("model_version", 7),
        )
        for field, value in cases:
            root = self.broken(**{field: value})
            for allow in (None, True):
                with self.subTest(field=field, value=value, allow=allow), self.with_claude():
                    with self.assertRaises(PlanningError) as raised:
                        route_capability("complex_implementation", risk="low", root=root, allow_candidate_bindings=allow)
                    message = raised.exception.message
                    self.assertIn(field, message)
                    self.assertIn("capability registry entry #", message)
                    self.assertIn("candidate", message)

    def test_the_loader_itself_refuses_so_no_caller_can_hit_a_keyerror(self) -> None:
        """Protects: load_candidates / load_registry validate; the error identifies the entry by position and what it does bind."""
        root = self.broken(model_id=None)
        with self.assertRaises(PlanningError) as raised:
            load_candidates(root)
        self.assertEqual(raised.exception.context["missing"], ["model_id"])
        self.assertEqual(raised.exception.context["capability_class"], "complex_implementation")
        index = [e["binding_type"] for e in REGISTRY["entries"]].index("candidate")
        self.assertEqual(raised.exception.context["entry_index"], index)
        with self.assertRaises(PlanningError):
            load_registry(root)

    def test_a_malformed_champion_entry_is_refused_the_same_way(self) -> None:
        """Protects: the validation is for every entry, not only candidates."""
        def drop_provider(entries):
            return [{k: v for k, v in e.items() if k != "provider"} if e["capability_class"] == "standard_implementation" else e
                    for e in entries]
        root = registry_fixture(self, entries=drop_provider)
        with self.assertRaises(PlanningError) as raised:
            route_capability("standard_implementation", root=root)
        self.assertIn("provider", raised.exception.message)
        self.assertIn("standard_implementation", raised.exception.message)

    def test_the_real_registry_is_valid(self) -> None:
        """Protects: the validation does not reject the shipped file."""
        self.assertEqual(len(load_candidates(SOURCE_ROOT)), 1)
        self.assertEqual(len(load_registry(SOURCE_ROOT)), 5)


if __name__ == "__main__":
    unittest.main()
