"""Public projection of immutable review evidence, including the 4417 regression.

These are file/packaging tests, not source research, visual approval or a Compose
run. The real review_materials/evidence/headless modules are imported; fixture
SVG, XML and PNG bytes stand in for artifacts validated by earlier stages.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import headless
import review_materials as materials

BASE = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
SOURCE = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h32v32z"/></svg>'
FINAL = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0H32V32z"/></svg>'
XML = b'<vector><path android:pathData="M0,0H32V32Z"/></vector>'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8u0AAAAASUVORK5CYII=')
DATE = "2026-09-28T18:23:37Z"
COMMIT = "b" * 40
PAGE = "https://bank.example/"
ASSET = "https://bank.example/favicon.svg"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


class ReviewProjection(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="review-projection-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.session = self.root / "session"
        self.session.mkdir()
        self.raw_path = "evidence/4417/research/official-home-favicon-light-32.svg"
        self.final_path = "evidence/4417/final.svg"
        self.saved_final_path = f"evidence/4417/candidate-{sha(FINAL)}.svg"
        self.base_path = "worktree/banks/Example_4417-ru/icon.svg"
        self.metadata = {"id": 4417, "title": "Example", "countryCode": "RU"}
        meta = self.put("worktree/banks/Example_4417-ru/info.json", encoded(self.metadata))
        self.put(self.base_path, BASE)
        self.put(self.raw_path, SOURCE)
        self.put(self.final_path, FINAL)
        self.put(self.saved_final_path, FINAL)
        self.diff = self.put("evidence/4417/candidate.diff", b'--- base/4417/icon.svg\n+++ candidate/4417/icon.svg\n')
        self.xml = self.put("evidence/4417/generated/composeResources/drawable/bank_4417.xml", XML)
        self.manifest = {
            "schemaVersion": 1, "pins": headless.PINS,
            "entries": [{"id": 4417, "sourceValidated": True,
                         "sourcePath": str(self.session / self.final_path),
                         "sourceSha256": sha(FINAL), "xmlSha256": sha(XML),
                         "resourceName": "bank_4417"}]}
        manifest = self.put("evidence/4417/generated/manifest.json", encoded(self.manifest))
        comparison = {theme: {"beforeAfter": self.put(f"evidence/4417/before-after-{theme}.png", PNG)}
                      for theme in ("light", "dark")}
        self.raw = {"path": self.raw_path, "iconHash": sha(SOURCE),
                    "sourceUrl": PAGE, "sourceAssetUrl": ASSET}
        self.candidate = {"path": self.final_path, "sha256": sha(FINAL), "sourceUrl": PAGE,
                          "sourceAssetUrl": ASSET, "rationale": "Official compact mark changed.",
                          "role": "Compact symbol", "variant": "Light favicon",
                          "transforms": ["Equivalent vector normalization."],
                          "detailsJudgement": "Separated bands retained.",
                          "proportionsJudgement": "No distortion.",
                          "visibleDiffExplanation": "White mark on blue instead of navy on white."}
        self.sources = [{"kind": "official", "url": PAGE, "assetUrl": ASSET,
                         "checkedAt": DATE, "independenceGroup": "Official site",
                         "observation": "Official site links the current compact mark."}]
        self.assessment = {aspect: {"finding": "confirmed", "basis": f"Supported {aspect}."}
                           for aspect in ("identity", "currency", "variant")}
        self.assessment.update(independenceRationale="One primary publication.",
                               uncertainties=["Old publication date unknown."])
        self.desktop = {"renderer": headless.RENDERER, "manifest": manifest["path"],
                        "manifestSha256": manifest["sha256"], "observedAt": DATE,
                        "visualComparison": "Final render matches source on both themes."}
        self.platforms = [{"target": platform, "status": "not-tested", "available": False,
                           "observation": "Terminal-only policy; no mobile pass claimed."}
                          for platform in ("android-compose", "android-native", "ios-compose", "ios-native")]
        evidence = {"baseCommit": "a" * 40, "baseIconHash": sha(BASE),
                    "metadataHash": meta["sha256"], "checkedAt": DATE,
                    "candidate": self.candidate, "desktop": self.desktop, "diff": self.diff,
                    "comparison": comparison, "platforms": self.platforms}
        before = {"candidate": self.raw, "sources": self.sources, "assessment": self.assessment}
        final = {"path": self.saved_final_path, "sha256": sha(FINAL), "iconHash": sha(FINAL)}
        after = {"sourceCandidate": self.raw, "candidate": final}
        self.bank = {"id": 4417, "title": "Example", "folder": "banks/Example_4417-ru",
                     "iconHash": sha(BASE), "metadataHash": meta["sha256"], "metadata": self.metadata}
        self.report = {"id": 4417, "outcome": "candidate", "before": {"proposals": [before]},
                       "after": {"proposals": [after]}, "evidence": evidence,
                       "candidate": {"path": self.saved_final_path, "sha256": sha(FINAL)},
                       "renderBinding": {"sourceSha256": sha(FINAL), "xmlSha256": sha(XML), **headless.PINS},
                       "provenance": {"baseCommit": "a" * 40, "metadataHash": meta["sha256"]}}

    def put(self, relative, data):
        path = self.session / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": relative, "sha256": sha(data)}

    def prepare(self):
        return materials.prepare(self.session, self.bank, self.report)

    def body(self, package=None):
        return materials.render(package or self.prepare(), "fixture", "catalog", COMMIT)

    def link(self, name):
        return f"[{name}](https://github.com/fixture/catalog/blob/{COMMIT}/review-materials/4417/{name})"

    def snapshot(self):
        return {str(path.relative_to(self.session)): path.read_bytes()
                for path in self.session.rglob("*") if path.is_file()}

    def first_icon(self):
        (self.session / self.base_path).unlink()
        self.bank["iconHash"] = None
        self.report["evidence"]["baseIconHash"] = None
        self.report["evidence"]["desktop"]["baseObservation"] = None
        from candidate import _canonical_diff
        self.report["evidence"]["diff"] = self.put("evidence/4417/candidate.diff",
            _canonical_diff(None, FINAL, 4417).encode())
        self.report["evidence"]["comparison"] = {
            theme: {"base": None, "beforeAfter": None,
                    "candidate": self.put(f"evidence/4417/final-{theme}.png", PNG + theme.encode())}
            for theme in ("light", "dark")}

    def test_first_icon_materials_bind_real_final_frames_without_baseline(self):
        self.first_icon()
        package = self.prepare()
        files = {row["name"]: row for row in package["files"]}
        self.assertEqual(set(files), {"source.svg", "final.svg", "candidate.diff", "final.xml",
                                      "comparison-light.png", "comparison-dark.png"})
        self.assertIsNone(package["details"]["baseSha256"])
        for theme in ("light", "dark"):
            self.assertEqual(files[f"comparison-{theme}.png"]["content"], PNG + theme.encode())
        self.assertEqual(files["final.svg"]["content"], FINAL)
        self.assertEqual(package["details"]["metadata"], self.metadata)
        body = self.body(package)
        self.assertIn("No existing icon.svg at frozen base commit", body)
        self.assertIn("First icon addition", body)
        self.assertNotIn("base.svg", body)
        self.assertNotIn("before/after", body)
        self.assertNotIn(sha(BASE), body)

    def test_first_icon_allowlist_rejects_fabricated_base_material(self):
        self.first_icon()
        package = self.prepare()
        package["files"].append({"name": "base.svg", "content": BASE,
                                  "sha256": sha(BASE), "size": len(BASE)})
        with self.assertRaisesRegex(ValueError, "allowlist"):
            self.body(package)

    def test_absent_local_baseline_is_not_projected_to_a_public_asset(self):
        self.first_icon()
        self.report["evidence"]["candidate"]["rationale"] = f"Reviewed {self.base_path}."
        body = self.body()
        self.assertNotIn(self.base_path, body)
        self.assertNotIn("/base.svg", body)
        self.assertFalse((self.session / self.base_path).exists())

    def test_addition_diff_header_does_not_exempt_local_paths_in_svg_content(self):
        self.first_icon()
        source = SOURCE.replace(b"</svg>", b"<!-- /dev/null --> </svg>")
        self.put(self.raw_path, source)
        self.raw["iconHash"] = sha(source)
        with self.assertRaisesRegex(ValueError, "local path"):
            self.prepare()

    def test_first_icon_review_rejects_appeared_baseline_without_overwrite(self):
        self.first_icon()
        self.put(self.base_path, BASE)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual((self.session / self.base_path).read_bytes(), BASE)

    def test_gazprombank_source_observations_no_longer_block_materials(self):
        preview = "evidence/4417/research/official-home-favicon-light-162.png"
        other = "evidence/4417/research/independent-bankiros-profile-gazprombank.svg"
        self.sources[0]["observation"] = (
            f"The HTTPS homepage links the current favicon. Preserved byte-for-byte at {self.raw_path} "
            f"(SHA-256 {sha(SOURCE)}). Source preview rasterized independently at {preview}. "
            "This is a source reference, not Compose evidence.")
        self.sources.append({**self.sources[0], "kind": "third-party", "url": "https://profile.example/4417",
                             "assetUrl": "https://profile.example/wordmark.svg",
                             "observation": f"Wordmark saved unchanged at {other}; supports identity only."})
        self.put("reports/4417.json", encoded(self.report))
        old_files, old_report, old_bank = self.snapshot(), copy.deepcopy(self.report), copy.deepcopy(self.bank)
        package = self.prepare()
        body = self.body(package)
        self.assertIn(self.link("source.svg"), body)
        self.assertIn("local artifact not published; reference omitted", body)
        self.assertIn(sha(SOURCE), body)
        self.assertIn("supports identity only", body)
        self.assertIn("not Compose evidence", body)
        self.assertNotIn("evidence/", body)
        self.assertNotIn(str(self.session), body)
        self.assertEqual(len(package["files"]), 7)
        self.assertEqual(old_files, self.snapshot())
        self.assertEqual(old_report, self.report)
        self.assertEqual(old_bank, self.bank)
        self.assertEqual(package, self.prepare())

    def test_relative_absolute_file_uri_and_quoted_aliases(self):
        aliases = (self.raw_path, "./" + self.raw_path, str(self.session / self.raw_path),
                   (self.session / self.raw_path).as_uri())
        for alias in aliases:
            for delimiter in ("", "`", '"', "'"):
                with self.subTest(alias=alias, delimiter=delimiter):
                    self.sources[0]["observation"] = f"Saved at {delimiter}{alias}{delimiter}; source is current."
                    projected = self.prepare()["details"]["sources"][0]["observation"]
                    self.assertIn({"material": "source.svg", "sha256": sha(SOURCE)}, projected)
                    body = self.body()
                    self.assertIn("Saved at " + self.link("source.svg") + "; source is current.", body)
                    self.assertNotIn(alias, body)

    def test_all_seven_roles_and_final_alias_use_exact_links(self):
        paths = [(self.base_path, "base.svg"), (self.raw_path, "source.svg"),
                 (self.final_path, "final.svg"), (self.saved_final_path, "final.svg"),
                 (self.diff["path"], "candidate.diff"), (self.xml["path"], "final.xml"),
                 ("evidence/4417/before-after-light.png", "comparison-light.png"),
                 ("evidence/4417/before-after-dark.png", "comparison-dark.png")]
        self.desktop["visualComparison"] = "Compared " + "; ".join(path for path, _ in paths) + "."
        body = self.body()
        for path, name in paths:
            self.assertNotIn(path, body)
            self.assertIn(self.link(name), body)

    def test_unpublished_reference_does_not_read_upload_or_substitute(self):
        self.put("evidence/4417/other/source.svg", SOURCE)
        self.sources[0]["observation"] = "Other evidence/4417/other/source.svg and evidence/4417/missing.png."
        original = Path.read_bytes
        reads = []
        def read(path):
            reads.append(path)
            return original(path)
        with patch.object(Path, "read_bytes", read):
            package = self.prepare()
        self.assertNotIn(self.session / "evidence/4417/other/source.svg", reads)
        self.assertNotIn(self.session / "evidence/4417/missing.png", reads)
        observation = package["details"]["sources"][0]["observation"]
        self.assertFalse(any(type(part) is dict for part in observation))
        self.assertEqual(sum("reference omitted" in part for part in observation), 2)
        self.assertEqual(len(package["files"]), 7)

    def test_known_path_prefix_is_not_enough_to_link_a_different_file(self):
        for ending in (".backup", "-other", "/child", "?download=1"):
            with self.subTest(ending=ending):
                self.sources[0]["observation"] = "Saved " + self.raw_path + ending
                value = self.prepare()["details"]["sources"][0]["observation"]
                self.assertFalse(any(type(part) is dict for part in value))

    def test_http_urls_and_ordinary_slashes_are_not_rewritten(self):
        text = f"Source https://example.org/{self.raw_path}; XML/painterResource, before/after, 54dp."
        self.sources[0]["observation"] = text
        self.assertEqual(self.prepare()["details"]["sources"][0]["observation"], text)
        self.assertIn(materials._text(text), self.body())

    def test_projection_covers_all_narrative_fields_only(self):
        text = "See " + self.raw_path
        self.sources[0]["observation"] = text
        self.sources[0]["independenceGroup"] = text
        for aspect in ("identity", "currency", "variant"):
            self.assessment[aspect]["basis"] = text
        self.assessment["independenceRationale"] = text
        self.assessment["uncertainties"] = [text]
        for key in ("rationale", "role", "variant", "detailsJudgement", "proportionsJudgement", "visibleDiffExplanation"):
            self.candidate[key] = text
        self.candidate["transforms"] = [text]
        self.desktop["visualComparison"] = text
        for platform in self.platforms:
            platform["observation"] = text
        package = self.prepare()
        self.assertNotIn("evidence/", json.dumps(package["details"]))
        self.assertEqual(package["details"]["sources"][0]["url"], PAGE)
        self.assertEqual(package["details"]["sources"][0]["assetUrl"], ASSET)
        self.assertEqual(package["details"]["metadata"], self.metadata)
        self.assertEqual(package["details"]["checkedAt"], DATE)
        self.assertEqual(package["details"]["assessment"]["identity"]["finding"], "confirmed")
        self.assertGreater(self.body(package).count(self.link("source.svg")), 15)

    def test_credentials_and_controls_rejected_before_projection(self):
        bad = ("ghp_" + "A" * 30, "github_pat_" + "A" * 30,
               "Authorization: Bearer secret", "https://user:password@example.org/x", "new\nline", "tab\tchar")
        for secret in bad:
            with self.subTest(secret=secret[:10]):
                self.sources[0]["observation"] = f"Saved evidence/4417/{secret}/local.txt"
                with self.assertRaisesRegex(ValueError, "credential|control"):
                    self.prepare()
        self.sources[0]["observation"] = self.session.as_uri() + "/evidence/4417/%67hp_" + "A" * 30
        with self.assertRaises(materials.PublicPreflightError) as caught:
            self.prepare()
        self.assertIn({'field': 'sources.0.observation', 'code': 'local-path'}, caught.exception.issues)
        self.assertNotIn(self.sources[0]["observation"], str(caught.exception))

    def test_external_and_traversal_paths_remain_blocking(self):
        bad = ("/home/private/secret.txt", "../evidence/4417/source.svg", "file:///etc/passwd",
               "C:\\private\\file.txt", "evidence/4417/../../secret.txt", "./evidence//secret.txt",
               str(self.session) + "/../outside.txt", self.session.as_uri() + "/evidence/%2e%2e/secrets")
        for path in bad:
            with self.subTest(path=path):
                self.sources[0]["observation"] = "Saved " + path
                with self.assertRaisesRegex(ValueError, "local|unsafe"):
                    self.prepare()

    def test_metadata_title_and_source_urls_are_not_sanitized(self):
        self.sources[0]["url"] = "file:///tmp/page.html"
        with self.assertRaisesRegex(ValueError, "local"):
            self.prepare()
        self.sources[0]["url"] = PAGE
        self.bank["title"] = "Name evidence/private.txt"
        with self.assertRaisesRegex(ValueError, "local"):
            self.prepare()
        self.bank["title"] = "Example"
        self.metadata["url"] = "file:///tmp/site.html"
        meta = self.put("worktree/banks/Example_4417-ru/info.json", encoded(self.metadata))
        self.bank["metadataHash"] = self.report["evidence"]["metadataHash"] = self.report["provenance"]["metadataHash"] = meta["sha256"]
        with self.assertRaisesRegex(ValueError, "local"):
            self.prepare()

    def test_reference_segments_cannot_be_injected_as_source_observations(self):
        self.sources[0]["observation"] = [{"material": "source.svg", "sha256": sha(SOURCE)}]
        with self.assertRaises(ValueError):
            self.prepare()
        self.sources[0]["observation"] = 'Literal {"material":"source.svg"} [[material:source.svg]]'
        value = self.prepare()["details"]["sources"][0]["observation"]
        self.assertIsInstance(value, str)
        self.assertIn(materials._text(value), self.body())

    def test_markdown_in_source_text_stays_escaped(self):
        self.sources[0]["observation"] = f"![bad](https://evil.example) <script> *claim* `{self.raw_path}`"
        body = self.body()
        self.assertIn(r"\!\[bad\]\(https://evil.example\)", body)
        self.assertIn(r"\<script\> \*claim\*", body)
        self.assertIn(self.link("source.svg"), body)
        self.assertNotIn("<script>", body)

    def test_forged_material_segments_rejected_when_rendering(self):
        package = self.prepare()
        for part in ({"material": "../secret.txt", "sha256": sha(SOURCE)},
                     {"material": "source.svg", "sha256": "0" * 64},
                     {"material": "source.svg", "sha256": sha(SOURCE), "url": "https://evil.example"},
                     {"material": "https://evil.example", "sha256": sha(SOURCE)},
                     ["nested"], 3):
            with self.subTest(part=part):
                package["details"]["sources"][0]["observation"] = [part]
                # Either the complete public scanner or the material allowlist may
                # reject first; the contract is no rendered forged reference.
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.body(package)
                self.assertEqual(self.snapshot(), before)

    def test_hash_mismatch_still_stops_before_public_projection(self):
        self.sources[0]["observation"] = "Saved " + self.raw_path
        (self.session / self.raw_path).write_bytes(SOURCE + b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.prepare()

    def test_symlink_still_stops_before_public_projection(self):
        source = self.session / self.raw_path
        external = self.root / "external.svg"
        external.write_bytes(SOURCE)
        source.unlink()
        source.symlink_to(external)
        self.sources[0]["observation"] = "Saved " + self.raw_path
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.prepare()

    def test_deterministic_json_round_trip_and_revalidation(self):
        self.sources[0]["observation"] = f"Saved {self.raw_path}; preview evidence/4417/local.png."
        package = self.prepare()
        public = {**package, "files": [{k: v for k, v in item.items() if k != "content"}
                                      for item in package["files"]]}
        roundtrip = json.loads(encoded(public))
        self.assertEqual(self.body(package), self.body(roundtrip))
        self.assertEqual(package, self.prepare())
        # The same-session retry reads the old report; it does not save new evidence.
        old = self.snapshot()
        for _ in range(3):
            self.prepare()
        self.assertEqual(self.snapshot(), old)

    def test_public_text_changes_only_narrative_without_mutating_evidence(self):
        baseline = self.prepare()
        self.sources[0]["observation"] = 'Homepage contains <img src="/wcms-resources/LOGO_RSHB.svg">.'
        self.put("reports/4417.json", encoded(self.report))
        old_files, old_report = self.snapshot(), copy.deepcopy(self.report)
        with self.assertRaises(ValueError):
            self.prepare()
        replacement = "Homepage displays the current compact bank mark."
        package = materials.prepare(self.session, self.bank, self.report,
                                    public_text={"sources.0.observation": replacement})
        expected = copy.deepcopy(baseline["details"])
        expected["sources"][0]["observation"] = replacement
        self.assertEqual(package["details"], expected)
        self.assertEqual(package["files"], baseline["files"])
        self.assertIn(replacement, self.body(package))
        self.assertNotIn("/wcms-resources/", self.body(package))
        self.assertEqual(self.report, old_report)
        self.assertEqual(self.snapshot(), old_files)

    def test_public_text_supports_existing_narrative_list_leaves(self):
        replacements = {"candidate.transforms.0": "Equivalent source normalization.",
                        "assessment.uncertainties.0": "Publication date remains unknown.",
                        "assessment.identity.basis": "Official compact mark matches bank identity.",
                        "platforms.0.observation": "Android rendering not tested.",
                        "visualComparison": "Both desktop themes match the source."}
        package = materials.prepare(self.session, self.bank, self.report, public_text=replacements)
        for field, replacement in replacements.items():
            value = package["details"]
            for part in field.split("."):
                value = value[int(part)] if isinstance(value, list) else value[part]
            self.assertEqual(value, replacement, field)
        self.assertEqual(package["details"]["platforms"][0]["status"], "not-tested")
        self.assertEqual(package["details"]["assessment"]["identity"]["finding"], "confirmed")

    def test_public_text_rejects_structured_unknown_and_invalid_leaves(self):
        invalid = [(key, "Replacement") for key in (
            "id", "metadata.title", "sourceUrl", "sourceSha256", "checkedAt",
            "sources.0.url", "sources.0.checkedAt", "assessment.identity.finding",
            "platforms.0.status", "sources.99.observation", "candidate.transforms.99",
            "candidate.unknown", "sources.00.observation")]
        invalid += [("sources.0.observation", value) for value in ("", "  ", None, 3, [], {})]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError) as error:
                    materials.prepare(self.session, self.bank, self.report, public_text={key: value})
                self.assertIn(key, str(error.exception))

    def test_public_text_does_not_bypass_public_scanner_or_artifact_checks(self):
        for replacement in ("Read /tmp/private/logo.svg", "Authorization: Bearer secret-token"):
            with self.subTest(replacement=replacement):
                with self.assertRaises(ValueError):
                    materials.prepare(self.session, self.bank, self.report,
                                      public_text={"sources.0.observation": replacement})
        (self.session / self.raw_path).write_bytes(SOURCE + b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            materials.prepare(self.session, self.bank, self.report,
                              public_text={"sources.0.observation": "Official mark observed."})


    def test_spaces_and_unicode_in_session_and_known_source_path(self):
        moved = self.root / "сессия с пробелами"
        self.session.rename(moved)
        self.session = moved
        old = self.raw_path
        self.raw_path = "evidence/4417/логотип (source) с пробелами.svg"
        self.put(self.raw_path, (self.session / old).read_bytes())
        self.raw["path"] = self.raw_path
        self.manifest["entries"][0]["sourcePath"] = str(self.session / self.final_path)
        manifest = self.put(self.desktop["manifest"], encoded(self.manifest))
        self.desktop["manifestSha256"] = manifest["sha256"]
        for alias in (self.raw_path, str(self.session / self.raw_path),
                      (self.session / self.raw_path).as_uri()):
            for quote in ('"', "`", ""):
                with self.subTest(alias=alias, quote=quote):
                    self.sources[0]["observation"] = f"Read {quote}{alias}{quote}."
                    self.assertIn("Read " + self.link("source.svg") + ".", self.body())
                    self.assertNotIn("пробелами", self.body())

    def test_unknown_quoted_paths_with_spaces_are_wholly_omitted(self):
        for quote in ('"', "`", "'"):
            self.sources[0]["observation"] = f"Read {quote}evidence/4417/private notes/another logo.svg{quote}; uncertain."
            body = self.body()
            self.assertNotIn("private notes", body)
            self.assertNotIn("another logo.svg", body)
            self.assertIn("reference omitted", body)
            self.assertIn("; uncertain.", body)

    def test_raster_source_cannot_be_relabelled_for_publication(self):
        from io import BytesIO
        from PIL import Image

        for extension, image_format in (("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")):
            buffer = BytesIO()
            Image.new("RGB", (1, 1), "white").save(buffer, format=image_format)
            content = buffer.getvalue()
            with self.subTest(extension=extension):
                self.put(self.raw_path, content)
                self.raw["iconHash"] = sha(content)
                self.sources[0]["observation"] = "Exact fetched source " + self.raw_path
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "original vector"):
                    self.prepare()
                self.assertEqual(self.snapshot(), before)

    def test_file_contents_are_never_redacted_to_bypass_publication_scan(self):
        content = SOURCE + b'<!-- saved at evidence/4417/private.svg -->'
        self.put(self.raw_path, content)
        self.raw["iconHash"] = sha(content)
        self.sources[0]["observation"] = "Saved " + self.raw_path
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "source.svg contains a local path"):
            self.prepare()
        self.assertEqual(self.snapshot(), before)

    def test_non_public_session_artifacts_stay_unlinked(self):
        paths = ("reports/4417.json", "asset-publication/receipt.json",
                 "worktree/banks/Example_4417-ru/info.json", "evidence/4417/generated/manifest.json")
        self.sources[0]["observation"] = "; ".join(str(self.session / p) for p in paths)
        value = self.prepare()["details"]["sources"][0]["observation"]
        self.assertFalse(any(type(part) is dict for part in value))
        self.assertEqual(sum("reference omitted" in part for part in value), len(paths))

    def test_absolute_sibling_session_reference_is_not_rebound(self):
        self.sources[0]["observation"] = str(self.session) + "-other/" + self.raw_path
        with self.assertRaisesRegex(ValueError, "local path"):
            self.prepare()

    def test_material_links_resolve_to_exact_files_in_a_real_git_commit(self):
        self.sources[0]["observation"] = "Fetched " + self.raw_path
        package = self.prepare()
        repo = self.root / "public-materials"
        repo.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                  capture_output=True).stdout
        git("init", "-q")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        for file in package["files"]:
            target = repo / package["details"]["materialPath"] / file["name"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(file["content"])
        git("add", ".")
        git("commit", "-qm", "seven exact review files")
        commit = git("rev-parse", "HEAD").decode().strip()
        body = materials.render(package, "fixture", "catalog", commit)
        for file in package["files"]:
            relative = package["details"]["materialPath"] + file["name"]
            self.assertEqual(git("show", f"{commit}:{relative}"), file["content"])
            self.assertIn(f"/blob/{commit}/{relative}", body)
        self.assertEqual(len(git("ls-tree", "-r", "--name-only", commit).splitlines()), 7)


if __name__ == "__main__":
    unittest.main()
