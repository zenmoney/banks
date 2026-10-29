"""CLI launch boundaries with a controlled exporter, not real rendering or agent proof."""
from session_fixture import make_session
import json
import os
from pathlib import Path
import shlex
import unittest
from unittest.mock import patch

import test_headless as headless


class TerminalOnlyExportCLI(unittest.TestCase):
    setUp = headless.HeadlessCLI.setUp
    bank = headless.HeadlessCLI.bank
    commit = headless.HeadlessCLI.commit
    cli = headless.HeadlessCLI.cli
    exporter = headless.HeadlessCLI.exporter
    export = headless.HeadlessCLI.export

    def gui_traps(self):
        directory = self.root / "forbidden-tools"
        directory.mkdir()
        marker = self.root / "gui-invocations"
        for name in ("chromium", "chromium-browser", "google-chrome", "firefox",
                     "playwright", "selenium", "xdg-open", "open", "display",
                     "Xvfb", "xvfb-run", "vncserver", "emulator", "screencapture"):
            executable = directory / name
            executable.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$0\" >> " + shlex.quote(str(marker)) + "\nexit 97\n",
                encoding="utf-8")
            executable.chmod(0o755)
        return {"PATH": str(directory) + os.pathsep + os.environ.get("PATH", ""),
                "DISPLAY": ":99", "WAYLAND_DISPLAY": "wayland-test",
                "XAUTHORITY": str(self.root / "no-authority"),
                "JAVA_TOOL_OPTIONS": "-Djava.awt.headless=false"}, marker

    def test_export_uses_explicit_headless_mode_even_with_gui_environment(self):
        make_session(self)
        self.catalog = self.exporter()
        before = (self.session / "session.json").read_bytes()
        environment, marker = self.gui_traps()
        with patch.dict(os.environ, environment):
            result = self.export()
            self.assertEqual(os.environ["DISPLAY"], ":99")
            self.assertEqual(os.environ["JAVA_TOOL_OPTIONS"], "-Djava.awt.headless=false")
        self.assertFalse(marker.exists(), "export attempted a prohibited executable")
        desktop = result["desktop"]
        receipt = json.loads((self.session / desktop["invocation"]["path"]).read_bytes())
        command = receipt["command"]
        self.assertEqual(Path(command[0]), self.catalog / "gradlew")
        self.assertIn(":catalog:run", command)
        arguments = [value.removeprefix("--args=") for value in command if value.startswith("--args=")]
        self.assertEqual(len(arguments), 1)
        self.assertEqual(shlex.split(arguments[0])[0], "export")
        self.assertEqual(receipt["environment"], {
            "JAVA_TOOL_OPTIONS": "-Djava.awt.headless=true",
            "unset": ["DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"],
        })
        for theme in ("light", "dark"):
            self.assertTrue((self.session / desktop[theme]["path"]).is_file())
        self.assertNotIn("visualComparison", desktop)
        self.assertEqual((self.session / "session.json").read_bytes(), before)

    def test_failed_export_retains_diagnostics_without_gui_fallback(self):
        make_session(self)
        self.catalog = self.exporter()
        (self.catalog / "gradlew").write_text("#!/bin/sh\necho 'controlled failure' >&2\nexit 23\n")
        before = (self.session / "session.json").read_bytes()
        environment, marker = self.gui_traps()
        with patch.dict(os.environ, environment):
            result = self.export(ok=False)
        self.assertIn("exit 23", result["error"])
        self.assertFalse(marker.exists(), "failed export attempted a GUI fallback")
        output = self.session / "evidence/10/export"
        self.assertTrue(list(output.rglob("process.log")))
        self.assertTrue(list(output.rglob("invocation.json")))
        self.assertFalse(list(output.rglob("desktop.json")))
        self.assertEqual((self.session / "session.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
