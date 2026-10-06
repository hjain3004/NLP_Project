"""The machine program DAG must exactly encode the canonical P3 fan-in."""

from __future__ import annotations

import json
import pathlib
import subprocess
import unittest


def _repo_root() -> pathlib.Path:
    return pathlib.Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


class ProgramDagTests(unittest.TestCase):
    def test_p3_supporting_tracks_cross_lane_edges_and_fan_in_are_exact_and_acyclic(self) -> None:
        document = json.loads(
            (_repo_root() / "agent-system/work/program-dag.json").read_text()
        )
        tracks = {item["track_id"]: item for item in document["p3_supporting_tracks"]}
        self.assertEqual(
            tracks["foundation-models"]["edges"],
            [
                ["P2-M0", "P3-M3"],
                ["P3-M3", "P3-M4"],
                ["P3-M4", "P3-M5"],
                ["P3-M5", "P3-M6"],
                ["P3-M6", "P3-M7"],
            ],
        )
        self.assertEqual(
            tracks["fixtures-evaluation-integration"]["edges"],
            [
                ["P3-D7", "P3-Q1"],
                ["P3-E3", "P3-Q1"],
                ["P3-T5", "P3-Q1"],
                ["P3-Q1", "P3-Q2"],
                ["P3-H2", "P3-Q3"],
                ["P3-D7", "P3-Q4"],
                ["P3-H3", "P3-Q4"],
                ["P3-Q1", "P3-Q5"],
                ["P3-Q2", "P3-Q5"],
                ["P3-Q3", "P3-Q5"],
                ["P3-Q4", "P3-Q5"],
                ["P3-D7", "P3-I"],
                ["P3-T5", "P3-I"],
                ["P3-E3", "P3-I"],
                ["P3-P5", "P3-I"],
                ["P3-H3", "P3-I"],
                ["P3-M7", "P3-I"],
                ["P3-Q5", "P3-I"],
            ],
        )

        edges = [edge for lane in document["p3_lanes"] for edge in lane["edges"]]
        edges += [edge for track in tracks.values() for edge in track["edges"]]
        nodes = {node for edge in edges for node in edge}
        incoming = {node: 0 for node in nodes}
        outgoing = {node: [] for node in nodes}
        for source, target in edges:
            incoming[target] += 1
            outgoing[source].append(target)
        ready = sorted(node for node, count in incoming.items() if count == 0)
        visited = []
        while ready:
            node = ready.pop(0)
            visited.append(node)
            for target in outgoing[node]:
                incoming[target] -= 1
                if incoming[target] == 0:
                    ready.append(target)
                    ready.sort()
        self.assertEqual(set(visited), nodes)


if __name__ == "__main__":
    unittest.main()
