import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


PREPARE = Path(__file__).resolve().parents[1] / "prepare-steps.py"
spec = importlib.util.spec_from_file_location("prepare_steps", PREPARE)
steps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(steps)


class StepConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="step-config-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "tasks.yaml"
        self.output = self.root / "plan"
        self.output.mkdir()

    def test_ambiguous_or_empty_instructions_are_rejected(self):
        for row in ({}, {"task": "unknown"}, {"task": None},
                    {"prompt": " "}, {"prompt": 42}, {"prompt": "a\0b"},
                    {"prompt_file": ""}, {"prompt_file": False},
                    {"prompt": "inline", "prompt_file": "file.md"},
                    {"task": "add-missing", "promt": "typo"}):
            with self.subTest(row=row):
                self.config.write_text(yaml.safe_dump({"steps": [row]}))
                with self.assertRaises(ValueError):
                    steps.load_steps(self.config)

    def test_invalid_later_step_does_not_publish_a_partial_plan(self):
        self.config.write_text("steps:\n  - task: add-missing\n  - prompt_file: missing.md\n")
        result = subprocess.run(
            [sys.executable, str(PREPARE), str(self.config), str(self.output)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("steps[2].prompt_file", result.stderr)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_duplicate_keys_and_non_list_sequences_are_not_silently_reinterpreted(self):
        for document in (
            "steps: []\n", "steps: {}\n", "steps: null\n", "{}\n",
            "steps: []\nextra: true\n", "steps: [\n",
            "steps: [{task: add-missing, task: merge-approved}]\n",
            "steps: [{prompt: first}]\nsteps: [{prompt: second}]\n",
            "steps: [just a string]\n",
        ):
            with self.subTest(document=document):
                self.config.write_text(document)
                with self.assertRaises((ValueError, yaml.YAMLError)):
                    steps.load_steps(self.config)

    def test_prompt_files_must_be_readable_nonempty_utf8_text(self):
        file = self.root / "prompt.md"
        self.config.write_text("steps:\n  - prompt_file: prompt.md\n")
        for contents in (b"", b" \n", b"\xff", b"a\x00b"):
            with self.subTest(contents=contents):
                file.write_bytes(contents)
                with self.assertRaises(ValueError):
                    steps.load_steps(self.config)
        file.unlink()
        file.mkdir()
        with self.assertRaises(ValueError):
            steps.load_steps(self.config)

    def test_yaml_cannot_execute_python_constructors(self):
        marker = self.root / "executed"
        self.config.write_text(f'!!python/object/apply:os.system ["touch {marker}"]\n')
        with self.assertRaises(yaml.YAMLError):
            steps.load_steps(self.config)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
