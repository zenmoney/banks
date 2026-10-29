"""Structural contracts, not a claim that a model followed the instructions."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]


class SkillStructure(unittest.TestCase):
    def test_entrypoint_has_activation_metadata(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        self.assertIn("name: bank-icon-maintenance", frontmatter)
        self.assertIn("description:", frontmatter)
        self.assertIn("compatibility:", frontmatter)
        self.assertIn("disable-model-invocation: true", frontmatter)

    def test_entrypoint_links_directly_to_existing_stage_contracts(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        targets = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
        self.assertTrue(targets)
        for target in targets:
            if "://" in target or target.startswith("#"):
                continue
            relative = target.split("#", 1)[0]
            with self.subTest(target=target):
                path = (ROOT / relative).resolve()
                self.assertTrue(path.is_relative_to(ROOT.resolve()))
                self.assertTrue(path.is_file(), relative)

    def test_behavior_cases_have_evidence_expectations_without_fabricated_results(self):
        data = json.loads((ROOT / "evals/evals.json").read_text(encoding="utf-8"))
        self.assertEqual(data["skill_name"], ROOT.name)
        cases = data["evals"]
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertIs(type(case["id"]), int)
                for field in ("name", "prompt", "expected_output", "fixture_notes"):
                    self.assertIsInstance(case[field], str)
                    self.assertTrue(case[field].strip())
                self.assertTrue(all(isinstance(item, str) and item.strip() for item in case["expectations"]))
                self.assertIsInstance(case["files"], list)
                self.assertNotIn("passed", case)
                self.assertNotIn("score", case)

    def test_ci_uses_pinned_read_only_actions(self):
        text = (REPO / ".github/workflows/bank-icon-maintenance.yml").read_text(encoding="utf-8")
        actions = re.findall(r"uses:\s+([^\s#]+)", text)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"actions/[\w-]+@[0-9a-f]{40}", action) for action in actions))
        self.assertIn("contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("continue-on-error", text)

    


if __name__ == "__main__":
    unittest.main()
