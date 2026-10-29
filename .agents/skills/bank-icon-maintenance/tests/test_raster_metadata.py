"""Real raster containers; compression bytes are not metadata or visual proof."""
from io import BytesIO
from pathlib import Path
import struct
import sys
import unittest
import zlib

from PIL import Image, features
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import review_materials as materials


def chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data +
            struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))


def png(*metadata):
    # One pixel with bytes '{/A'. No text or local filename in the image.
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\x00{/A", level=0)) + b"".join(metadata) + chunk(b"IEND", b""))


def raster(format, **options):
    output = BytesIO()
    Image.new("RGB", (2, 2), (123, 47, 65)).save(output, format=format, **options)
    return output.getvalue()


class RasterMetadata(unittest.TestCase):
    def scan(self, content, format="png"):
        materials._scan_material("source." + format, content, Path("/private/session"), raster_format=format)

    def test_pixel_only_png_passes_without_reencoding(self):
        content = png()
        self.assertIsNotNone(materials._LOCAL.search(content.decode("utf-8", errors="replace")))
        before = materials._hash(content)
        self.scan(content)
        self.assertEqual(materials._hash(content), before)
        with Image.open(BytesIO(content)) as image:
            self.assertEqual(image.getpixel((0, 0)), (123, 47, 65))
            self.assertEqual(image.info, {})

    def test_png_text_after_image_and_duplicate_keywords_are_checked(self):
        for secret in (b"/private/session/input.svg", b"ghp_" + b"X" * 30, b"file:///tmp/private"):
            with self.subTest(secret_kind=secret[:4]):
                data = png(chunk(b"tEXt", b"Comment\0" + secret), chunk(b"tEXt", b"Comment\0benign"))
                with self.assertRaisesRegex(ValueError, "source.png metadata PNG chunk"):
                    self.scan(data)

    def test_compressed_and_international_png_text_is_checked(self):
        variants = [chunk(b"zTXt", b"Comment\0\0" + zlib.compress(b"/home/private/file")),
                    chunk(b"iTXt", b"Comment\0\1\0en\0Label\0" + zlib.compress(b"/home/private/file")),
                    chunk(b"iTXt", b"Comment\0\0\0en\0Label\0/home/private/file")]
        for metadata in variants:
            with self.subTest(kind=metadata[4:8]):
                with self.assertRaisesRegex(ValueError, "metadata"):
                    self.scan(png(metadata))

    def test_png_keyword_is_checked_even_with_benign_text(self):
        with self.assertRaisesRegex(ValueError, "keyword"):
            self.scan(png(chunk(b"tEXt", b"/private/file\0benign")))

    def test_benign_text_and_binary_transparency_pass(self):
        self.scan(png(chunk(b"tEXt", b"Comment\0Bank mark")))
        self.scan(raster("PNG"))
        image = Image.new("P", (1, 1))
        output = BytesIO()
        image.save(output, format="PNG", transparency=bytes(range(256)))
        self.scan(output.getvalue())

    def test_invalid_truncated_and_opaque_png_are_not_approved(self):
        for data in (b"\x89PNG\r\n\x1a\nnot an image", png()[:-6], png() + b"/tmp/extra",
                     png(chunk(b"raNd", b"opaque private metadata"))):
            with self.subTest(size=len(data)):
                with self.assertRaises(ValueError):
                    self.scan(data)

    def test_compressed_metadata_bomb_is_rejected(self):
        with self.assertRaises(ValueError):
            self.scan(png(chunk(b"zTXt", b"Comment\0\0" + zlib.compress(b"a" * (1024 * 1024 + 1)))))

    def test_jpeg_pixels_and_benign_metadata_pass(self):
        self.scan(raster("JPEG", comment=b"Bank mark"), "jpg")
        self.scan(raster("JPEG", progressive=True), "jpg")

    def test_jpeg_comments_are_checked_not_only_the_last_comment(self):
        data = raster("JPEG")
        secret = b"/private/session/file.svg"
        comment = b"\xff\xfe" + struct.pack(">H", len(secret) + 2) + secret
        with self.assertRaisesRegex(ValueError, "metadata.*comment"):
            self.scan(data[:2] + comment + data[2:], "jpg")

    def test_exif_text_on_supported_rasters_is_checked(self):
        for format, extension in (("PNG", "png"), ("JPEG", "jpg")):
            with self.subTest(format=format):
                exif = Image.Exif()
                exif[270] = "/home/private/input.svg"
                with self.assertRaisesRegex(ValueError, "EXIF"):
                    self.scan(raster(format, exif=exif), extension)
                exif[270] = "Public bank mark"
                self.scan(raster(format, exif=exif), extension)

    def test_unicode_exif_comment_is_decoded(self):
        exif = Image.Exif()
        exif[34665] = {37510: b"UNICODE\0" + "/home/private/input.svg".encode("utf-16-le")}
        with self.assertRaisesRegex(ValueError, "EXIF"):
            self.scan(raster("JPEG", exif=exif), "jpg")

    @unittest.skipUnless(features.check("littlecms2"), "installed Pillow has no ICC decoder")
    def test_valid_colour_profiles_are_not_scanned_as_binary_text(self):
        from PIL import ImageCms
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        for format, extension in (("PNG", "png"), ("JPEG", "jpg")):
            with self.subTest(format=format):
                self.scan(raster(format, icc_profile=profile), extension)

    @unittest.skipUnless(features.check("webp"), "installed Pillow has no WebP decoder")
    def test_webp_pixels_pass_and_text_metadata_is_checked(self):
        self.scan(raster("WEBP", lossless=True), "webp")
        self.scan(raster("WEBP", xmp=b"<xmp>Bank mark</xmp>"), "webp")
        with self.assertRaisesRegex(ValueError, "XMP"):
            self.scan(raster("WEBP", xmp=b"<xmp> /private/session/input.svg</xmp>"), "webp")

    def test_wrong_decoded_format_and_trailing_jpeg_are_rejected(self):
        with self.assertRaises(ValueError):
            self.scan(raster("JPEG"), "png")
        with self.assertRaises(ValueError):
            self.scan(raster("JPEG") + b"/private/session/input", "jpg")

    def test_text_materials_keep_the_existing_path_and_credential_checks(self):
        for name in ("base.svg", "source.svg", "final.svg", "candidate.diff", "final.xml"):
            for text in (b"/private/session/input", b"ghp_" + b"X" * 30):
                with self.subTest(name=name):
                    with self.assertRaisesRegex(ValueError, "local path or credential-like"):
                        materials._scan_material(name, text, Path("/private/session"))


if __name__ == "__main__":
    unittest.main()
