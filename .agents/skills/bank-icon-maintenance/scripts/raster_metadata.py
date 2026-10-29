"""Inspect public raster metadata, never compressed pixels as UTF-8 text.

No re-encoding, stripping, I/O, network or visual judgment. Container boundaries
identify metadata; Pillow validates the actual image and decodes EXIF/ICC fields.
Opaque metadata is rejected rather than silently declared free of local data.
"""
from io import BytesIO
import struct
import warnings
import zlib

MAX_TEXT = 1024 * 1024


def _inflate(value):
    decoder = zlib.decompressobj()
    plain = decoder.decompress(value, MAX_TEXT + 1)
    if len(plain) > MAX_TEXT or not decoder.eof or decoder.unused_data:
        raise ValueError("invalid or oversized compressed image metadata")
    return plain


def _decoded(value, encoding="utf-8"):
    if len(value) > MAX_TEXT:
        raise ValueError("oversized image metadata")
    return value.decode(encoding).rstrip("\0")


def _exif(value, check, label):
    from PIL import Image
    exif = Image.Exif()
    exif.load(value)
    # Numeric IFD identifiers avoid an enum-version requirement. No MakerNote
    # decoding: vendor-private data requires a separate inspected source.
    groups = [("IFD0", dict(exif)), ("Exif", exif.get_ifd(34665)),
              ("GPS", exif.get_ifd(34853)), ("IFD1", exif.get_ifd(-1))]
    if 40965 in groups[1][1]:
        groups.append(("Interop", exif.get_ifd(40965)))
    for group, fields in groups:
        for tag, item in fields.items():
            field = f"{label}.{group}.{tag}"
            if isinstance(item, str):
                check(field, item)
            elif tag in (40091, 40092, 40093, 40094, 40095):
                check(field, _decoded(bytes(item), "utf-16-le"))
            elif isinstance(item, bytes):
                if tag == 37510 or (group == "GPS" and tag in (27, 28)):
                    prefix, body = item[:8], item[8:]
                    if prefix == b"ASCII\0\0\0":
                        check(field, _decoded(body, "ascii"))
                    elif prefix == b"JIS\0\0\0\0\0":
                        check(field, _decoded(body, "shift_jis"))
                    elif prefix == b"UNICODE\0":
                        if body.startswith((b"\xff\xfe", b"\xfe\xff")):
                            check(field, _decoded(body, "utf-16"))
                        else:
                            # Both byte orders are seen without a BOM. Inspect
                            # both interpretations, not null-interleaved bytes.
                            check(field, _decoded(body, "utf-16-le"))
                            check(field, _decoded(body, "utf-16-be"))
                    else:
                        raise ValueError(f"{field}: unsupported EXIF text encoding")
                elif tag in (36864, 40960):  # EXIF/Flashpix version strings.
                    check(field, _decoded(item, "ascii"))
                elif tag == 37121 and len(item) == 4:
                    pass  # ComponentsConfiguration, not text.
                elif tag in (41728, 41729) and len(item) == 1:
                    pass  # FileSource/SceneType, not text.
                else:
                    raise ValueError(f"{field}: opaque EXIF metadata needs review")


def _icc(value, check, label):
    if len(value) > MAX_TEXT:
        raise ValueError("oversized ICC metadata")
    try:
        from PIL import ImageCms
    except ImportError as exc:
        raise ValueError("ICC metadata inspection requires the installed Pillow colour-management component") from exc
    profile = ImageCms.ImageCmsProfile(BytesIO(value)).profile
    for key in ("profile_description", "copyright", "manufacturer", "model",
                "screening_description", "viewing_condition"):
        item = getattr(profile, key, None)
        if isinstance(item, str):
            check(f"{label}.{key}", item)


def _png(content, check):
    offset = 8
    binary = {b"IHDR", b"IDAT", b"PLTE", b"tRNS", b"bKGD", b"cHRM", b"gAMA",
              b"sRGB", b"sBIT", b"pHYs", b"tIME", b"hIST", b"acTL", b"fcTL", b"fdAT"}
    while offset < len(content):
        size = int.from_bytes(content[offset:offset + 4], "big")
        kind = content[offset + 4:offset + 8]
        end = offset + 12 + size
        if len(kind) != 4 or end > len(content):
            raise ValueError("invalid PNG chunk boundary")
        data = content[offset + 8:end - 4]
        label = f"PNG chunk at {offset}"
        if kind in (b"tEXt", b"zTXt", b"iTXt", b"iCCP"):
            key, body = data.split(b"\0", 1)
            check(label + ".keyword", _decoded(key, "latin-1"))
            if kind == b"tEXt":
                check(label + ".text", _decoded(body, "latin-1"))
            elif kind in (b"zTXt", b"iCCP"):
                if not body or body[0] != 0:
                    raise ValueError("unsupported PNG metadata compression")
                plain = _inflate(body[1:])
                if kind == b"iCCP":
                    _icc(plain, check, label + ".ICC")
                else:
                    check(label + ".text", _decoded(plain, "latin-1"))
            else:
                if len(body) < 2 or body[0] not in (0, 1) or body[1] != 0:
                    raise ValueError("invalid PNG international text")
                language, translated, text = body[2:].split(b"\0", 2)
                check(label + ".language", _decoded(language, "ascii"))
                check(label + ".translated", _decoded(translated))
                check(label + ".text", _decoded(_inflate(text) if body[0] else text))
        elif kind == b"eXIf":
            _exif(data, check, label + ".EXIF")
        elif kind == b"IEND":
            if size or end != len(content):
                raise ValueError("unexpected bytes after PNG image")
            return
        elif kind not in binary:
            raise ValueError(f"{label}: unsupported metadata chunk")
        offset = end
    raise ValueError("PNG image has no end marker")


def _jpeg(content, check):
    offset = 2
    profiles = {}
    profile_count = None
    while offset < len(content):
        if content[offset] != 255:
            raise ValueError("invalid JPEG marker boundary")
        while offset < len(content) and content[offset] == 255:
            offset += 1
        if offset >= len(content):
            break
        marker = content[offset]
        offset += 1
        if marker == 217:
            if offset != len(content):
                raise ValueError("unexpected bytes after JPEG image")
            if profiles:
                if set(profiles) != set(range(1, profile_count + 1)):
                    raise ValueError("incomplete ICC profile in JPEG")
                _icc(b"".join(profiles[i] for i in sorted(profiles)), check, "JPEG.ICC")
            return
        if marker == 1 or 208 <= marker <= 215:
            continue
        size = int.from_bytes(content[offset:offset + 2], "big")
        if size < 2 or offset + size > len(content):
            raise ValueError("invalid JPEG segment size")
        data = content[offset + 2:offset + size]
        label = f"JPEG segment at {offset}"
        offset += size
        if marker == 254:
            check(label + ".comment", _decoded(data, "latin-1"))
        elif 224 <= marker <= 239:
            if marker == 225 and data.startswith(b"Exif\0\0"):
                _exif(data, check, label + ".EXIF")
            elif marker == 225 and data.startswith(b"http://ns.adobe.com/xap/1.0/\0"):
                check(label + ".XMP", _decoded(data.split(b"\0", 1)[1]))
            elif marker == 226 and data.startswith(b"ICC_PROFILE\0"):
                if len(data) < 14 or data[12] == 0 or data[13] == 0 or data[12] > data[13]:
                    raise ValueError("invalid JPEG ICC segment")
                if data[12] in profiles or profile_count not in (None, data[13]):
                    raise ValueError("duplicate or inconsistent JPEG ICC segments")
                profiles[data[12]], profile_count = data[14:], data[13]
            elif marker == 224 and data.startswith(b"JFIF\0"):
                if len(data) < 14 or len(data) != 14 + 3 * data[12] * data[13]:
                    raise ValueError("invalid JFIF thumbnail record")
                # Structured dimensions/thumbnail pixels, not prose.
            elif marker == 238 and data.startswith(b"Adobe") and len(data) == 12:
                pass  # Adobe colour-transform record.
            else:
                raise ValueError(f"{label}: opaque application metadata needs review")
        if marker == 218:  # Skip entropy data; stuffed FF and restart markers are pixels.
            while offset < len(content):
                pos = content.find(b"\xff", offset)
                if pos < 0:
                    raise ValueError("unterminated JPEG image data")
                following = pos + 1
                while following < len(content) and content[following] == 255:
                    following += 1
                if following >= len(content):
                    raise ValueError("unterminated JPEG marker")
                if content[following] == 0 or 208 <= content[following] <= 215:
                    offset = following + 1
                else:
                    offset = pos
                    break
    raise ValueError("JPEG image has no end marker")


def _webp(content, check):
    if int.from_bytes(content[4:8], "little") + 8 != len(content):
        raise ValueError("invalid WebP container size")
    offset = 12
    while offset < len(content):
        kind = content[offset:offset + 4]
        size = int.from_bytes(content[offset + 4:offset + 8], "little")
        end = offset + 8 + size
        if offset + 8 > len(content) or len(kind) != 4 or end + size % 2 > len(content):
            raise ValueError("invalid WebP chunk boundary")
        data = content[offset + 8:end]
        if kind == b"EXIF":
            _exif(data, check, "WebP.EXIF")
        elif kind == b"XMP ":
            check("WebP.XMP", _decoded(data))
        elif kind == b"ICCP":
            _icc(data, check, "WebP.ICC")
        elif kind not in {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF"}:
            raise ValueError("unsupported WebP metadata chunk")
        offset = end + size % 2


def inspect_metadata(content, format, check):
    """Call check(field, text) on decoded metadata; preserve source bytes exactly."""
    from PIL import Image
    expected = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}
    if format not in expected or len(content) > 16 * 1024 * 1024:
        raise ValueError("unsupported or oversized raster source")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with Image.open(BytesIO(content)) as image:
                if image.format != expected[format]:
                    raise ValueError("raster content does not match its detected format")
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
            {"png": _png, "jpg": _jpeg, "webp": _webp}[format](content, check)
    except (OSError, SyntaxError, UnicodeError, struct.error, zlib.error, Warning,
            TypeError, KeyError, IndexError) as exc:
        # Do not echo raw metadata or a credential-bearing decoder diagnostic.
        raise ValueError(f"invalid raster image or metadata ({type(exc).__name__})") from exc
