import json
import os
from pathlib import Path
import tempfile
import unittest
from xml.dom import minidom
import zipfile

from tools.export_drawable import ARCHIVE_NAME, SVG_NAMESPACE, export_drawable


SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="54" height="54"><path d="M0 0h54v54z" fill="#123456"/></svg>'


class ExportDrawableTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.banks = self.root / "banks"
        self.banks.mkdir()
        self.output = self.root / "export"

    def add_bank(self, name, bank_id, svg=SVG, banks=None):
        directory = (banks if banks is not None else self.banks) / name
        directory.mkdir()
        (directory / "info.json").write_text(
            json.dumps({"id": bank_id, "title": name, "plugin": "not-a-resource-id"}),
            encoding="utf-8",
        )
        if svg is not None:
            (directory / "icon.svg").write_bytes(svg if isinstance(svg, bytes) else svg.encode("utf-8"))
        return directory

    def exported_document(self, bank_id):
        document = minidom.parse(str(self.output / "drawable" / f"ic_bank_{bank_id}_54px.svg"))
        self.addCleanup(document.unlink)
        return document

    def test_metadata_ids_name_resources_and_metadata_only_banks_are_skipped(self):
        self.add_bank("Folder_999-unknown", 42)
        self.add_bank("Other", 7)
        self.add_bank("No icon", 100, svg=None)
        self.add_bank("Negative metadata-only", -1, svg=None)

        self.assertEqual(export_drawable(self.banks, self.output), (2, 2))
        expected = ["drawable/ic_bank_42_54px.svg", "drawable/ic_bank_7_54px.svg"]
        with zipfile.ZipFile(self.output / ARCHIVE_NAME) as archive:
            self.assertEqual(archive.namelist(), expected)
            for name in expected:
                self.assertEqual(archive.read(name), (self.output / name).read_bytes())
        self.assertEqual(
            sorted(path.name for path in (self.output / "drawable").iterdir()),
            [Path(name).name for name in expected],
        )

    def test_non_square_viewbox_and_origin_are_preserved_without_stretching(self):
        source = '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" viewBox="-10, 5, 120, 40"><path d="M-10 5h120v40z"/></svg>'
        bank = self.add_bank("Wide", 1, source)
        export_drawable(self.banks, self.output)

        root = self.exported_document(1).documentElement
        self.assertEqual(root.getAttribute("viewBox"), "-10, 5, 120, 40")
        self.assertEqual(root.getAttribute("width"), "54")
        self.assertEqual(root.getAttribute("height"), "54")
        self.assertEqual(root.getAttribute("preserveAspectRatio"), "xMidYMid meet")
        self.assertEqual(root.getElementsByTagName("path")[0].getAttribute("d"), "M-10 5h120v40z")
        self.assertEqual((bank / "icon.svg").read_text(encoding="utf-8"), source)

    def test_missing_viewbox_is_inferred_from_original_dimensions(self):
        self.add_bank("Wide", 1, '<svg xmlns="http://www.w3.org/2000/svg" width="120.5px" height="4e1"><path d="M0 0h120.5v40z"/></svg>')
        export_drawable(self.banks, self.output)
        root = self.exported_document(1).documentElement
        self.assertEqual(root.getAttribute("viewBox"), "0 0 120.5 4e1")
        self.assertEqual((root.getAttribute("width"), root.getAttribute("height")), ("54", "54"))
        self.assertEqual(root.getAttribute("preserveAspectRatio"), "xMidYMid meet")

    def test_viewbox_alone_supplies_geometry(self):
        self.add_bank("Only viewBox", 1, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="2 -3 18 36"><path d="M2 -3h18v36z"/></svg>')
        export_drawable(self.banks, self.output)
        root = self.exported_document(1).documentElement
        self.assertEqual(root.getAttribute("viewBox"), "2 -3 18 36")
        self.assertEqual((root.getAttribute("width"), root.getAttribute("height")), ("54", "54"))

    def test_masks_local_references_namespaces_and_alpha_are_retained(self):
        source = '''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="54" height="54" fill="none" xml:space="preserve">
<!-- keep artwork -->
<defs><path id="mark" d="M1 2h40v20z"/><mask id="fade"><path fill="#fff" fill-opacity="0.45" d="M0 0h54v54z"/></mask><style><![CDATA[.brand { fill: #1257ab; }]]></style></defs>
<use class="brand" xlink:href="#mark" mask="url(#fade)" opacity="0.7"/>
</svg>'''
        self.add_bank("Masked", 1, source)
        export_drawable(self.banks, self.output)
        document = self.exported_document(1)
        root = document.documentElement
        self.assertEqual(root.namespaceURI, SVG_NAMESPACE)
        self.assertEqual(root.getAttribute("fill"), "none")
        self.assertEqual(root.getAttributeNS("http://www.w3.org/XML/1998/namespace", "space"), "preserve")
        self.assertEqual(root.getAttribute("xmlns:xlink"), "http://www.w3.org/1999/xlink")
        self.assertEqual([node.localName for node in root.childNodes if node.nodeType == node.ELEMENT_NODE], ["defs", "use"])
        self.assertEqual(root.getElementsByTagName("mask")[0].getAttribute("id"), "fade")
        self.assertEqual(root.getElementsByTagName("mask")[0].getElementsByTagName("path")[0].getAttribute("fill-opacity"), "0.45")
        use = root.getElementsByTagName("use")[0]
        self.assertEqual(use.getAttributeNS("http://www.w3.org/1999/xlink", "href"), "#mark")
        self.assertEqual(use.getAttribute("mask"), "url(#fade)")
        self.assertEqual(use.getAttribute("opacity"), "0.7")
        self.assertIn("<![CDATA[.brand { fill: #1257ab; }]]>", document.toxml())
        self.assertIn("<!-- keep artwork -->", document.toxml())

    def test_duplicate_ids_are_rejected_even_for_metadata_only_banks(self):
        for index, (first_icon, second_icon) in enumerate(((True, True), (True, False), (False, True), (False, False))):
            with self.subTest(first_icon=first_icon, second_icon=second_icon):
                banks = self.root / f"duplicates-{index}"
                banks.mkdir()
                self.add_bank("First", 42, SVG if first_icon else None, banks)
                self.add_bank("Second", 42, SVG if second_icon else None, banks)
                output = self.root / f"duplicates-output-{index}"
                with self.assertRaisesRegex(ValueError, "duplicate bank id 42"):
                    export_drawable(banks, output)
                self.assertFalse(output.exists())

    def test_invalid_metadata_ids_are_not_coerced(self):
        for index, bank_id in enumerate((True, "42", 42.0, None)):
            with self.subTest(bank_id=bank_id):
                banks = self.root / f"invalid-id-{index}"
                banks.mkdir()
                self.add_bank("Invalid", bank_id, banks=banks)
                output = self.root / f"invalid-id-output-{index}"
                with self.assertRaises(ValueError):
                    export_drawable(banks, output)
                self.assertFalse(output.exists())

    def test_negative_icon_id_is_rejected(self):
        self.add_bank("Negative", -42)
        with self.assertRaisesRegex(ValueError, "negative id"):
            export_drawable(self.banks, self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_source_leaves_no_successful_archive_or_partial_export(self):
        invalid_sources = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="54" height="54">',
            '<svg width="54" height="54"/>',
            '<path xmlns="http://www.w3.org/2000/svg" width="54" height="54"/>',
        ]
        for attributes in (
            'width="0" height="54"',
            'width="-1" height="54"',
            'width="NaN" height="54"',
            'width="1e999" height="54"',
            'width="100%" height="54"',
            'width="54"',
            'viewBox="0 0 54"',
            'viewBox="0 0 54 54 extra"',
            'viewBox="0,,0 54 54"',
            'viewBox="0 0 54 54,"',
            'viewBox="0 0 0 54"',
            'viewBox="0 0 54 -1"',
            'viewBox="0 0 inf 54"',
            'viewBox="1e999 0 54 54"',
            'width="-1" viewBox="0 0 54 54"',
        ):
            invalid_sources.append(f'<svg xmlns="http://www.w3.org/2000/svg" {attributes}/>')
        for index, source in enumerate(invalid_sources):
            with self.subTest(source=source):
                banks = self.root / f"invalid-svg-{index}"
                banks.mkdir()
                self.add_bank("A valid first", 1, banks=banks)
                self.add_bank("Z invalid second", 2, source, banks)
                output = self.root / f"invalid-svg-output-{index}"
                with self.assertRaises(ValueError):
                    export_drawable(banks, output)
                self.assertFalse(output.exists())
                self.assertFalse((output / ARCHIVE_NAME).exists())

    def test_dtd_and_entities_are_rejected_in_utf8_and_utf16(self):
        sources = [
            '<!DOCTYPE svg SYSTEM "file:///not-to-be-opened">' + SVG,
            '<!DOCTYPE svg [<!ENTITY shape "expanded">]><svg xmlns="http://www.w3.org/2000/svg" width="54" height="54"><text>&shape;</text></svg>',
        ]
        for index, (source, encoding) in enumerate((source, encoding) for source in sources for encoding in ("utf-8", "utf-16")):
            with self.subTest(source=source, encoding=encoding):
                banks = self.root / f"doctype-{index}"
                banks.mkdir()
                self.add_bank("Unsafe", 1, source.encode(encoding), banks)
                output = self.root / f"doctype-output-{index}"
                with self.assertRaisesRegex(ValueError, "DTD and entity declarations"):
                    export_drawable(banks, output)
                self.assertFalse(output.exists())

    def test_existing_output_is_never_modified(self):
        self.add_bank("Valid", 1)
        self.output.mkdir()
        sentinel = self.output / "keep"
        sentinel.write_text("untouched", encoding="utf-8")
        with self.assertRaises(ValueError):
            export_drawable(self.banks, self.output)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")
        self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_existing_dangling_output_symlink_is_not_followed(self):
        self.add_bank("Valid", 1)
        target = self.root / "absent"
        self.output.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            export_drawable(self.banks, self.output)
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(target.exists())

    def test_input_and_output_cannot_overlap_even_through_symlinks(self):
        self.add_bank("Valid", 1)
        alias = self.root / "input-alias"
        alias.symlink_to(self.banks, target_is_directory=True)
        for output in (self.banks, self.banks / "new-export", alias / "new-export", self.root):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    export_drawable(self.banks, output)
        self.assertFalse((self.banks / "new-export").exists())
        self.assertTrue((self.banks / "Valid" / "icon.svg").is_file())

    def test_metadata_only_and_empty_inputs_do_not_publish_empty_archives(self):
        with self.assertRaisesRegex(ValueError, "no bank icons"):
            export_drawable(self.banks, self.output)
        self.add_bank("Metadata only", 1, svg=None)
        with self.assertRaisesRegex(ValueError, "no bank icons"):
            export_drawable(self.banks, self.output)
        self.assertFalse(self.output.exists())

    def test_archive_is_reproducible_independent_of_source_timestamps(self):
        bank = self.add_bank("Valid", 1)
        self.add_bank("Another", 2)
        export_drawable(self.banks, self.output)
        original = (self.output / ARCHIVE_NAME).read_bytes()
        os.utime(bank / "icon.svg", (1_000_000, 1_000_000))
        another_output = self.root / "another-export"
        export_drawable(self.banks, another_output)
        self.assertEqual((another_output / ARCHIVE_NAME).read_bytes(), original)
        with zipfile.ZipFile(self.output / ARCHIVE_NAME) as archive:
            self.assertEqual(archive.namelist(), sorted(archive.namelist()))
            for entry in archive.infolist():
                self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(entry.external_attr >> 16, 0o100644)


if __name__ == "__main__":
    unittest.main()
