"""External CLI regressions with disposable Git and controlled provider/renders.

These never inspect live banks, publish to GitHub, or alter an operator policy.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_attempts as attempts
import test_candidates as candidates
import test_asset_publication as publication
from test_raster_metadata import png, chunk


class RasterSourcePublication(unittest.TestCase):
    def test_raster_or_embedded_raster_source_cannot_prepare_replacement(self):
        sources = (png(), png(chunk(b"tEXt", b"Comment\0/home/private/input.svg")),
                   b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,AAAA"/></svg>')
        for content in sources:
            with self.subTest(source=content[:20]):
                f = candidates.CandidateCLI("runTest")
                self.addCleanup(f.doCleanups)
                f.raw_source = content
                f.setUp()
                before = (f.session / "session.json").read_bytes()
                result = f.cli("continue", "--candidate", str(f.package_path), ok=False)
                self.assertIn("original vector", result["error"])
                self.assertEqual((f.session / "session.json").read_bytes(), before)
                self.assertEqual((f.session / "evidence/10/raw.svg").read_bytes(), content)
                self.assertFalse((f.session / "asset-publication").exists())


if __name__ == "__main__":
    unittest.main()
