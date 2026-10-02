package catalog.generator

import com.google.gson.JsonParser
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import kotlin.io.path.readText
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import org.junit.jupiter.api.io.TempDir

class CatalogGeneratorTest {
    @TempDir lateinit var root: Path

    private val svg = """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="20" viewBox="0 0 24 20"><path fill="#123456" d="M0 0h24v20H0z"/></svg>"""

    private fun bank(folder: String, id: Int, title: String, image: String = svg): Path {
        val directory = Files.createDirectories(root.resolve("banks/$folder"))
        Files.writeString(directory.resolve("info.json"), """{"id":$id,"title":"$title"}""")
        Files.writeString(directory.resolve("icon.svg"), image)
        return directory
    }

    private fun generate(vararg extra: String): Pair<Int, Path> {
        val output = root.resolve("generated")
        return run(arrayOf("--banks", root.resolve("banks").toString(), "--output", output.toString(), *extra)) to output
    }

    private fun manifest(output: Path) = JsonParser.parseString(output.resolve("manifest.json").readText()).asJsonObject
    private fun entry(output: Path) = manifest(output).getAsJsonArray("entries")[0].asJsonObject
    private fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    @Test fun `valid bank uses metadata ID, source geometry, exact hashes and a converted resource`() {
        val source = bank("WrongFolderName_42-ru", 42, "A ${'$'} quoted bank")
        val (code, output) = generate()
        assertEquals(0, code)
        val item = entry(output)
        assertEquals(1, manifest(output).get("schemaVersion").asInt)
        assertEquals(42, item.get("id").asInt)
        assertEquals("A ${'$'} quoted bank", item.get("title").asString)
        assertEquals("24", item.get("sourceWidth").asString)
        assertEquals("20", item.get("sourceHeight").asString)
        assertEquals("0 0 24 20", item.get("viewBox").asString)
        assertEquals(24f, item.get("naturalWidth").asFloat)
        assertEquals("bank_42", item.get("resourceName").asString)
        assertEquals(sha(Files.readAllBytes(source.resolve("icon.svg"))), item.get("sourceSha256").asString)
        val xml = output.resolve("composeResources/drawable/bank_42.xml")
        assertTrue(xml.readText().contains("<vector"))
        assertEquals(sha(Files.readAllBytes(xml)), item.get("xmlSha256").asString)
    }
    @Test fun `metadata-only duplicate ID blocks the existing icon instead of publishing a resource`() {
        bank("First_7-ru", 7, "First")
        val second = bank("Second_7-ru", 7, "Second")
        Files.delete(second.resolve("icon.svg"))
        val (code, output) = generate()
        assertEquals(0, code)
        assertEquals(1, manifest(output).getAsJsonArray("entries").size())
        assertTrue(entry(output).get("error").asString.contains("Duplicate bank ID"))
        assertTrue(entry(output).get("resourceName").isJsonNull)
        assertFalse(Files.exists(output.resolve("composeResources/drawable/bank_7.xml")))
    }
    @Test fun `local use converts without losing the referenced path`() {
        bank("LocalUse_8-ru", 8, "LocalUse", """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><defs><path id="shape" d="M0 0h20v20H0z"/></defs><use href="#shape"/></svg>""")
        bank("DefsOnly_25-ru", 25, "DefsOnly", """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><defs><path id="shape" d="M0 0h20v20H0z"/></defs></svg>""")
        val (code, output) = generate()
        val item = entry(output)
        assertEquals(0, code)
        assertTrue(item.get("error").isJsonNull)
        assertEquals("bank_8", item.get("resourceName").asString)
        assertTrue(output.resolve("composeResources/drawable/bank_8.xml").readText().contains("pathData"))
        val defsXml = output.resolve("composeResources/drawable/bank_25.xml").takeIf { Files.exists(it) }?.readText().orEmpty()
        assertFalse(defsXml.contains("pathData"), "Unreferenced defs must not draw the shape")
    }
    @Test fun `candidate under output is refused without changing its bytes`() {
        bank("Original_9-ru", 9, "Original")
        val candidate = root.resolve("generated/candidate.svg")
        Files.createDirectories(candidate.parent)
        Files.writeString(candidate, svg)
        val before = Files.readAllBytes(candidate)
        val (code, output) = generate("--id", "9", "--candidate", candidate.toString())
        assertEquals(2, code)
        assertTrue(before.contentEquals(Files.readAllBytes(candidate)))
        assertFalse(Files.exists(output.resolve("manifest.json")))
    }
    @Test fun `folder ID disagreement blocks resource even for unknown country`() {
        bank("Listed_11-unknown", 10, "Listed")
        val (code, output) = generate("--id", "10")
        assertEquals(1, code)
        assertTrue(entry(output).get("error").asString.contains("Folder ID"))
        assertTrue(entry(output).get("resourceName").isJsonNull)
    }
    @Test fun `selected candidate changes only the render input and preserves accepted SVG`() {
        val accepted = bank("Original_12-ru", 12, "Original").resolve("icon.svg")
        val original = Files.readAllBytes(accepted)
        val candidate = root.resolve("candidate.svg")
        Files.writeString(candidate, svg.replace("#123456", "#654321"))
        val (code, output) = generate("--id", "12", "--candidate", candidate.toString())
        assertEquals(0, code)
        assertTrue(original.contentEquals(Files.readAllBytes(accepted)))
        assertEquals(sha(Files.readAllBytes(candidate)), entry(output).get("sourceSha256").asString)
        assertEquals(candidate.toString(), entry(output).get("sourcePath").asString)
        assertTrue(output.resolve("composeResources/drawable/bank_12.xml").readText().contains("654321"))
        assertEquals(1, manifest(output).getAsJsonArray("entries").size())
    }
    @Test fun `candidate renders metadata-only bank without creating an accepted icon`() {
        val directory = bank("NoImage_26-ru", 26, "NoImage")
        Files.delete(directory.resolve("icon.svg"))
        val metadata = Files.readAllBytes(directory.resolve("info.json"))
        val candidate = root.resolve("candidate.svg")
        Files.writeString(candidate, svg)
        val (code, output) = generate("--id", "26", "--candidate", candidate.toString())
        assertEquals(0, code)
        val item = entry(output)
        assertTrue(item.get("error").isJsonNull)
        assertTrue(item.get("sourceValidated").asBoolean)
        assertEquals(candidate.toString(), item.get("sourcePath").asString)
        assertEquals(sha(Files.readAllBytes(candidate)), item.get("sourceSha256").asString)
        val xml = output.resolve("composeResources/drawable/bank_26.xml")
        assertEquals(sha(Files.readAllBytes(xml)), item.get("xmlSha256").asString)
        assertTrue(xml.readText().contains("pathData"))
        assertFalse(Files.exists(directory.resolve("icon.svg")))
        assertTrue(metadata.contentEquals(Files.readAllBytes(directory.resolve("info.json"))))
    }
    @Test fun `candidate cannot bypass metadata or duplicate ID errors for metadata-only banks`() {
        val cases = listOf(
            "Wrong_99-ru" to """{"id":27,"title":"Wrong"}""",
            "Wrong_27-ru" to """{"id":27,"title":"Wrong","countryCode":"us"}""",
            "Wrong_27-ru" to """{"id":27,"title":"Wrong","countryCode":7}""",
            "Wrong_27-ru" to """{"id":27,"title":""}""",
            "Wrong_27-ru" to """{"id":"27","title":"Wrong"}"""
        )
        val candidate = root.resolve("candidate.svg")
        Files.writeString(candidate, svg)
        for ((folder, metadata) in cases) {
            val directory = Files.createDirectories(root.resolve("banks/$folder"))
            Files.writeString(directory.resolve("info.json"), metadata)
            val (code, output) = generate("--id", "27", "--candidate", candidate.toString())
            assertTrue(code != 0, metadata)
            assertFalse(Files.exists(output.resolve("composeResources/drawable/bank_27.xml")))
            assertFalse(Files.exists(directory.resolve("icon.svg")))
            Files.delete(directory.resolve("info.json"))
            Files.delete(directory)
        }
        for (folder in listOf("First_27-ru", "Second_27-ru")) {
            val directory = Files.createDirectories(root.resolve("banks/$folder"))
            Files.writeString(directory.resolve("info.json"), """{"id":27,"title":"Listed"}""")
        }
        val (code, output) = generate("--id", "27", "--candidate", candidate.toString())
        assertEquals(1, code)
        assertTrue(manifest(output).getAsJsonArray("entries").all { !it.asJsonObject.get("error").isJsonNull })
        assertFalse(Files.exists(output.resolve("composeResources/drawable/bank_27.xml")))
    }
    @Test fun `candidate never creates an unknown bank or falls back to accepted SVG`() {
        val accepted = bank("Existing_28-ru", 28, "Existing").resolve("icon.svg")
        val original = Files.readAllBytes(accepted)
        val candidate = root.resolve("candidate.svg")
        Files.writeString(candidate, svg)
        val (unknownCode, unknownOutput) = generate("--id", "29", "--candidate", candidate.toString())
        assertEquals(2, unknownCode)
        assertFalse(Files.exists(unknownOutput.resolve("manifest.json")))
        Files.delete(candidate)
        val (missingCode, missingOutput) = generate("--id", "28", "--candidate", candidate.toString())
        assertEquals(1, missingCode)
        assertFalse(Files.exists(missingOutput.resolve("composeResources/drawable/bank_28.xml")))
        Files.writeString(candidate, svg.replace("<path", "<image href=\"photo.png\"/><path"))
        val (unsafeCode, unsafeOutput) = generate("--id", "28", "--candidate", candidate.toString())
        assertEquals(1, unsafeCode)
        assertFalse(Files.exists(unsafeOutput.resolve("composeResources/drawable/bank_28.xml")))
        assertTrue(original.contentEquals(Files.readAllBytes(accepted)))
    }
    @Test fun `unsafe SVG and converter diagnostics remain errors without drawable resources`() {
        val cases = listOf(
            """<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">&x;</svg>""",
            """<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><script>alert(1)</script></svg>""",
            """<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><path onclick="run()" d="M0 0h10v10z"/></svg>""",
            """<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><use href="https://example.test/shape"/></svg>""",
            """<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><path fill="url(https://example.test/paint)" d="M0 0h10v10z"/></svg>""",
            """<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><image href="data:image/png;base64,AAAA"/></svg>""",
            """<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><foreignObject/></svg>""",
            """<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><animate attributeName="opacity"/></svg>""",
            """<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><text x="0" y="10">not a vector path</text></svg>"""
        )
        cases.forEachIndexed { index, image -> bank("Unsafe_${index + 101}-ru", index + 101, "Unsafe", image) }
        val (code, output) = generate()
        assertEquals(0, code)
        manifest(output).getAsJsonArray("entries").forEach { value ->
            val item = value.asJsonObject
            assertFalse(item.get("error").isJsonNull, "ID ${item.get("id")}")
            assertTrue(item.get("resourceName").isJsonNull)
            assertFalse(Files.exists(output.resolve("composeResources/drawable/bank_${item.get("id").asInt}.xml")))
        }
    }
    @Test fun `manifest distinguishes validated unsupported SVG from unsafe source with a hash`() {
        val unsafe = bank("Unsafe_31-ru", 31, "Unsafe", """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><script>alert(1)</script></svg>""").resolve("icon.svg")
        val invalidDimensions = bank("Invalid_32-ru", 32, "Invalid", """<svg xmlns="http://www.w3.org/2000/svg" width="0" height="24"><path d="M0 0h24v24H0z"/></svg>""").resolve("icon.svg")
        val unsupported = bank("Masked_33-ru", 33, "Masked", """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><defs><mask id="soft"><rect width="24" height="24" fill="#fff" opacity="0.5"/></mask></defs><rect width="24" height="24" mask="url(#soft)"/></svg>""").resolve("icon.svg")
        val (code, output) = generate()
        assertEquals(0, code)
        assertEquals(1, manifest(output).get("schemaVersion").asInt)
        val items = manifest(output).getAsJsonArray("entries").associate { value ->
            val item = value.asJsonObject
            item.get("id").asInt to item
        }
        for ((id, source, validated) in listOf(Triple(31, unsafe, false), Triple(32, invalidDimensions, false), Triple(33, unsupported, true))) {
            val item = items.getValue(id)
            assertEquals(sha(Files.readAllBytes(source)), item.get("sourceSha256").asString)
            assertEquals(validated, item.get("sourceValidated").asBoolean, "ID $id")
            assertFalse(item.get("error").isJsonNull, "ID $id")
            assertTrue(item.get("resourceName").isJsonNull, "ID $id")
            assertFalse(Files.exists(output.resolve("composeResources/drawable/bank_$id.xml")))
        }
    }
    @Test fun `missing icon is skipped in bulk but selected bank has a retained source error`() {
        val withoutImage = bank("NoImage_14-ru", 14, "NoImage")
        Files.delete(withoutImage.resolve("icon.svg"))
        bank("Visible_15-ru", 15, "Visible")
        val (bulkCode, bulkOutput) = generate()
        assertEquals(0, bulkCode)
        assertEquals(1, manifest(bulkOutput).getAsJsonArray("entries").size())
        val (selectedCode, selectedOutput) = generate("--id", "14")
        assertEquals(1, selectedCode)
        assertFalse(entry(selectedOutput).get("error").isJsonNull)
        assertTrue(entry(selectedOutput).get("sourceSha256").isJsonNull)
        assertTrue(entry(selectedOutput).get("resourceName").isJsonNull)
    }
    @Test fun `explicit dimensions and viewBox stay distinct while percentages remain ambiguous`() {
        bank("Viewport_16-ru", 16, "Viewport", """<svg xmlns="http://www.w3.org/2000/svg" width="54px" height="54" viewBox="0 0 55 55"><path d="M0 0h55v55H0z"/></svg>""")
        bank("Percent_17-ru", 17, "Percent", """<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="54" viewBox="0 0 55 55"><path d="M0 0h55v55H0z"/></svg>""")
        val (code, output) = generate()
        assertEquals(0, code)
        val rows = manifest(output).getAsJsonArray("entries").map { it.asJsonObject }.associateBy { it.get("id").asInt }
        assertEquals("54px", rows.getValue(16).get("sourceWidth").asString)
        assertEquals("0 0 55 55", rows.getValue(16).get("viewBox").asString)
        assertEquals(54f, rows.getValue(16).get("naturalWidth").asFloat)
        assertEquals("100%", rows.getValue(17).get("sourceWidth").asString)
        assertTrue(rows.getValue(17).get("naturalWidth").isJsonNull)
        assertTrue(rows.getValue(17).get("naturalHeight").isJsonNull)
    }
    @Test fun `generated symlink cannot overwrite accepted source`() {
        val accepted = bank("Protected_18-ru", 18, "Protected").resolve("icon.svg")
        val original = Files.readAllBytes(accepted)
        val drawable = Files.createDirectories(root.resolve("generated/composeResources/drawable"))
        val link = drawable.resolve("bank_18.xml")
        Files.createSymbolicLink(link, accepted)
        val (code, output) = generate("--id", "18")
        assertEquals(2, code)
        assertTrue(original.contentEquals(Files.readAllBytes(accepted)))
        assertTrue(Files.isSymbolicLink(link))
        assertFalse(Files.exists(output.resolve("manifest.json")))
    }
    @Test fun `output below accepted banks is rejected before any writes`() {
        val accepted = bank("Protected_19-ru", 19, "Protected").resolve("icon.svg")
        val original = Files.readAllBytes(accepted)
        val output = root.resolve("banks/output")
        assertEquals(2, run(arrayOf("--banks", root.resolve("banks").toString(), "--output", output.toString(), "--id", "19")))
        assertFalse(Files.exists(output))
        assertTrue(original.contentEquals(Files.readAllBytes(accepted)))
    }
    @Test fun `malformed country metadata fails one bank without aborting bulk`() {
        val malformed = bank("Malformed_21-ru", 21, "Malformed")
        Files.writeString(malformed.resolve("info.json"), """{"id":21,"title":"Malformed","countryCode":{}}""")
        bank("Valid_22-ru", 22, "Valid")
        val (code, output) = generate()
        assertEquals(0, code)
        val rows = manifest(output).getAsJsonArray("entries").map { it.asJsonObject }.associateBy { it.get("id").asInt }
        assertTrue(rows.getValue(21).get("error").asString.contains("countryCode"))
        assertTrue(rows.getValue(21).get("resourceName").isJsonNull)
        assertEquals("bank_22", rows.getValue(22).get("resourceName").asString)
    }
    @Test fun `regeneration removes only previously generated resources`() {
        val source = bank("Gone_23-ru", 23, "Gone").resolve("icon.svg")
        val (firstCode, output) = generate()
        assertEquals(0, firstCode)
        val drawable = output.resolve("composeResources/drawable")
        val unrelated = drawable.resolve("bank_999.xml")
        Files.writeString(unrelated, "keep")
        Files.delete(source)
        val (secondCode, _) = generate()
        assertEquals(0, secondCode)
        assertFalse(Files.exists(drawable.resolve("bank_23.xml")))
        assertEquals("keep", unrelated.readText())
    }
    @Test fun `safe local gradient survives converter into drawable`() {
        bank("Gradient_24-ru", 24, "Gradient", """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><defs><linearGradient id="paint"><stop offset="0%" stop-color="#ff0000"/><stop offset="100%" stop-color="#0000ff"/></linearGradient></defs><path fill="url(#paint)" d="M0 0h24v24H0z"/></svg>""")
        val (code, output) = generate("--id", "24")
        assertEquals(0, code)
        assertTrue(entry(output).get("error").isJsonNull)
        val xml = output.resolve("composeResources/drawable/bank_24.xml").readText()
        assertTrue(xml.contains("gradient"))
        assertTrue(xml.contains("FF0000", ignoreCase = true))
        assertTrue(xml.contains("0000FF", ignoreCase = true))
    }
    @Test fun `nonpositive explicit SVG dimensions fail before drawable generation`() {
        bank("Zero_26-ru", 26, "Zero", """<svg xmlns="http://www.w3.org/2000/svg" width="0" height="24"><path d="M0 0h24v24H0z"/></svg>""")
        val (code, output) = generate("--id", "26")
        assertEquals(1, code)
        assertTrue(entry(output).get("error").asString.contains("Invalid SVG width"))
        assertTrue(entry(output).get("resourceName").isJsonNull)
    }
    @Test fun `animation variants are rejected before conversion`() {
        bank("Animated_27-ru", 27, "Animated", """<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><path d="M0 0h24v24H0z"><animateColor attributeName="fill" from="red" to="blue" dur="1s"/></path></svg>""")
        val (code, output) = generate("--id", "27")
        assertEquals(1, code)
        assertTrue(entry(output).get("error").asString.contains("Unsafe SVG element"))
        assertTrue(entry(output).get("resourceName").isJsonNull)
    }
    @Test fun `negative integer ID has a valid stable drawable name`() {
        bank("Negative_-28-ru", -28, "Negative")
        val (code, output) = generate("--id", "-28")
        assertEquals(0, code)
        assertEquals(-28, entry(output).get("id").asInt)
        assertEquals("bank_m28", entry(output).get("resourceName").asString)
        assertTrue(Files.exists(output.resolve("composeResources/drawable/bank_m28.xml")))
    }
}
