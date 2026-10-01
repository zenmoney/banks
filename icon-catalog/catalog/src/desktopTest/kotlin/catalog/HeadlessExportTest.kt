package catalog

import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.awt.image.BufferedImage
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import javax.imageio.ImageIO
import java.util.concurrent.TimeUnit
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertTrue

class HeadlessExportTest {
    @Test
    fun unknownIdFailsWithoutPublishingOutput() {
        val temp = Files.createTempDirectory("headless-unknown-id-")
        try {
            val manifest = temp.resolve("manifest.json")
            Files.writeString(manifest, """{"schemaVersion":1,"entries":[]}""")
            val output = temp.resolve("export")

            val cli = runCli(arrayOf(
                "export", "--id", "912345678", "--manifest", manifest.toString(),
                "--output", output.toString(),
            ), temp)

            assertNotEquals(0, cli.status, "An absent ID must not be reported as a render success")
            assertTrue(cli.output.contains("Bank ID 912345678"), cli.output)
            assertFalse(Files.exists(output), "Failure must not publish even an empty output directory")
        } finally {
            deleteTree(temp)
        }
    }

    @Test
    fun exportsRealCompiledDrawableOnBothBackgroundsWithExactProvenance() {
        val temp = Files.createTempDirectory("headless-render-")
        try {
            val fixture = fixture(temp)
            val entry = fixture.entry
            val source = fixture.source
            val manifestPath = fixture.manifestPath
            val output = temp.resolve("export")

            val cli = runCli(arrayOf(
                "export", "--id", entry.get("id").asString, "--manifest", manifestPath.toString(),
                "--output", output.toString(),
            ), temp)
            assertEquals(0, cli.status, "A matching compiled XML, source and manifest must render successfully: " + cli.output)

            val result = JsonParser.parseString(Files.readString(output.resolve("result.json"))).asJsonObject
            assertEquals("success", result.get("status").asString)
            assertEquals(entry.get("id").asInt, result.get("id").asInt)
            assertEquals(1, result.get("schemaVersion").asInt)
            assertEquals(3f, result.get("density").asFloat)
            assertEquals(240, result.get("sizePx").asInt)
            assertEquals(54f, result.get("iconSizeDp").asFloat)
            assertEquals(sha(source), result.getAsJsonObject("source").get("sha256").asString)
            assertEquals(source.toString(), result.getAsJsonObject("source").get("path").asString)
            assertEquals(sha(manifestPath), result.getAsJsonObject("manifest").get("sha256").asString)
            assertEquals(manifestPath.toString(), result.getAsJsonObject("manifest").get("path").asString)
            val xml = result.getAsJsonObject("xml")
            assertEquals(entry.get("resourceName").asString, xml.get("resourceName").asString)
            assertEquals(entry.get("xmlSha256").asString, xml.get("sha256").asString)
            val resourcePath = "composeResources/catalog.resources/drawable/${entry.get("resourceName").asString}.xml"
            val loadedBytes = requireNotNull(javaClass.classLoader.getResourceAsStream(resourcePath)) { "Compiled drawable $resourcePath is absent" }.use { it.readAllBytes() }
            assertEquals(sha(loadedBytes), xml.get("loadedSha256").asString)
            assertTrue(result.get("renderer").asString.contains("offscreen", ignoreCase = true))
            assertTrue(result.get("renderer").asString.contains("Skia", ignoreCase = true))
            assertTrue(result.has("pins") && result.has("runtime") && result.has("observedAt"))

            val images = result.getAsJsonArray("images").associate { it.asJsonObject.get("theme").asString to it.asJsonObject }
            assertEquals(setOf("light", "dark"), images.keys)
            for ((theme, background) in listOf("light" to 0xFFFFFF, "dark" to 0x121212)) {
                val imageRecord = requireNotNull(images[theme])
                val imagePath = output.resolve("$theme.png")
                assertEquals("$theme.png", imageRecord.get("path").asString)
                assertEquals(sha(imagePath), imageRecord.get("sha256").asString)
                val image: BufferedImage = requireNotNull(ImageIO.read(imagePath.toFile()))
                assertEquals(240, image.width)
                assertEquals(240, image.height)
                assertEquals(background, image.getRGB(0, 0) and 0xFFFFFF, "The $theme image must have the requested background")
                assertTrue((39 until 201).any { y -> (39 until 201).any { x ->
                    (image.getRGB(x, y) and 0xFFFFFF) != background
                } }, "$theme must contain an icon, not just its background")
            }
        } finally {
            deleteTree(temp)
        }
    }

    @Test
    fun changedSourceBytesCannotReuseCompiledResult() {
        val temp = Files.createTempDirectory("headless-source-mismatch-")
        try {
            val fixture = fixture(temp)
            Files.writeString(fixture.source, "\n<!-- source changed after compilation -->", java.nio.file.StandardOpenOption.APPEND)
            val output = temp.resolve("export")

            val cli = runCli(arrayOf(
                "export", "--id", fixture.entry.get("id").asString,
                "--manifest", fixture.manifestPath.toString(), "--output", output.toString(),
            ), temp)

            assertNotEquals(0, cli.status, "A stale compiled icon cannot certify different source SVG bytes")
            assertTrue(cli.output.contains("SVG source hash differs"), cli.output)
            assertFalse(Files.exists(output.resolve("result.json")), "A failed source binding must not publish success")
        } finally {
            deleteTree(temp)
        }
    }

    @Test
    fun changedLoadedXmlBytesAreRejectedEvenWhenManifestAndCompiledEntryAgree() {
        val temp = Files.createTempDirectory("headless-loaded-xml-")
        try {
            val fixture = fixture(temp)
            val resourcePath = "composeResources/catalog.resources/drawable/${fixture.entry.get("resourceName").asString}.xml"
            val override = temp.resolve("resources")
            val alteredXml = override.resolve(resourcePath)
            Files.createDirectories(alteredXml.parent)
            javaClass.classLoader.getResourceAsStream(resourcePath).use { original ->
                Files.write(alteredXml, requireNotNull(original).readAllBytes())
            }
            Files.writeString(alteredXml, "\n", java.nio.file.StandardOpenOption.APPEND)
            val output = temp.resolve("export")

            val cli = runCli(arrayOf(
                "export", "--id", fixture.entry.get("id").asString,
                "--manifest", fixture.manifestPath.toString(), "--output", output.toString(),
            ), temp, override)

            assertNotEquals(0, cli.status, "The loaded XML bytes differed from the compiled hash")
            assertTrue(cli.output.contains("Actually loaded painter XML hash"), cli.output)
            assertFalse(Files.exists(output.resolve("result.json")), "Loaded XML mismatch must not publish success")
        } finally {
            deleteTree(temp)
        }
    }

    @Test
    fun malformedCompiledDrawableCannotPublishPlaceholderAsSuccess() {
        val temp = Files.createTempDirectory("headless-malformed-xml-")
        try {
            val fixture = fixture(temp)
            val resourcePath = "composeResources/catalog.resources/drawable/${fixture.entry.get("resourceName").asString}.xml"
            val override = temp.resolve("resources")
            val brokenXml = override.resolve(resourcePath)
            Files.createDirectories(brokenXml.parent)
            Files.writeString(brokenXml, "<vector broken")
            val output = temp.resolve("export")

            val cli = runCli(arrayOf(
                "export", "--id", fixture.entry.get("id").asString,
                "--manifest", fixture.manifestPath.toString(), "--output", output.toString(),
            ), temp, override)

            assertNotEquals(0, cli.status, "Malformed loaded VectorDrawable must not render")
            assertTrue(cli.output.contains("Painter loading failed"), cli.output)
            assertFalse(Files.exists(output.resolve("result.json")), "Placeholder or failed decode is never a success")
        } finally {
            deleteTree(temp)
        }
    }

    @Test
    fun generatedConverterErrorCannotBeExported() {
        val temp = Files.createTempDirectory("headless-converter-error-")
        try {
            val original = JsonParser.parseString(Files.readString(Path.of(requireNotNull(System.getProperty("catalog.generatedManifest"))))).asJsonObject
            val compiled = generatedCatalog().associateBy { it.id }
            val failed = original.getAsJsonArray("entries").map { it.asJsonObject }.first { row ->
                val diagnostic = row.get("error")
                diagnostic != null && !diagnostic.isJsonNull && diagnostic.asString.contains("Svg2Vector") &&
                    compiled[row.get("id").asInt]?.error == diagnostic.asString
            }
            val sourcePath = Path.of(failed.get("sourcePath").asString)
            val source = temp.resolve(sourcePath)
            Files.createDirectories(source.parent)
            Files.copy(Path.of(original.get("sourceRoot").asString).resolve(sourcePath), source)
            val manifest = original.deepCopy().apply {
                addProperty("sourceRoot", temp.toString())
                add("entries", JsonArray().apply { add(failed.deepCopy()) })
            }
            val manifestPath = temp.resolve("manifest.json")
            Files.writeString(manifestPath, manifest.toString())
            val output = temp.resolve("export")

            val cli = runCli(arrayOf(
                "export", "--id", failed.get("id").asString,
                "--manifest", manifestPath.toString(), "--output", output.toString(),
            ), temp)

            assertNotEquals(0, cli.status, "A real converter-error catalog entry must not export")
            assertTrue(cli.output.contains("Svg2Vector"), cli.output)
            assertFalse(Files.exists(output.resolve("result.json")))
        } finally {
            deleteTree(temp)
        }
    }

    @Test
    fun existingOutputDirectoryIsLeftUntouched() {
        val temp = Files.createTempDirectory("headless-existing-output-")
        try {
            val fixture = fixture(temp)
            val output = Files.createDirectory(temp.resolve("export"))
            val sentinel = output.resolve("result.json")
            Files.writeString(sentinel, "previous reviewed result")
            Files.writeString(output.resolve("light.png"), "previous light image")

            val cli = runCli(arrayOf(
                "export", "--id", fixture.entry.get("id").asString,
                "--manifest", fixture.manifestPath.toString(), "--output", output.toString(),
            ), temp)

            assertNotEquals(0, cli.status, "The export must not overwrite an existing result")
            assertTrue(cli.output.contains("fresh directory"), cli.output)
            assertEquals("previous reviewed result", Files.readString(sentinel))
            assertEquals("previous light image", Files.readString(output.resolve("light.png")))
            assertFalse(Files.exists(output.resolve("dark.png")))
        } finally {
            deleteTree(temp)
        }
    }

    @Test
    fun destinationWriteFailureNeverPublishesSuccess() {
        val temp = Files.createTempDirectory("headless-write-failure-")
        try {
            val fixture = fixture(temp)
            val blockedParent = temp.resolve("not-a-directory")
            Files.writeString(blockedParent, "must remain unchanged")
            val output = blockedParent.resolve("export")

            val cli = runCli(arrayOf(
                "export", "--id", fixture.entry.get("id").asString,
                "--manifest", fixture.manifestPath.toString(), "--output", output.toString(),
            ), temp)

            assertNotEquals(0, cli.status, "A failed output-directory reservation cannot be success")
            assertTrue(cli.output.contains("Desktop export failed"), cli.output)
            assertEquals("must remain unchanged", Files.readString(blockedParent))
            assertFalse(Files.exists(output.resolve("result.json")))
        } finally {
            deleteTree(temp)
        }
    }

    @Test
    fun absentCompiledXmlCannotBeReportedAsRendered() {
        val temp = Files.createTempDirectory("headless-missing-xml-")
        try {
            val fixture = fixture(temp)
            val name = fixture.entry.get("resourceName").asString
            val resourcePath = "composeResources/catalog.resources/drawable/$name.xml"
            val output = temp.resolve("export")

            val cli = runCli(arrayOf(
                "export", "--id", fixture.entry.get("id").asString,
                "--manifest", fixture.manifestPath.toString(), "--output", output.toString(),
            ), temp, omitResourcePath = resourcePath)

            assertNotEquals(0, cli.status, "Compiled descriptor without XML cannot render")
            assertTrue(cli.output.contains("Painter loading failed"), cli.output)
            assertTrue(cli.output.contains(resourcePath), cli.output)
            assertFalse(Files.exists(output.resolve("result.json")), "Missing painter XML must not publish success")
        } finally {
            deleteTree(temp)
        }
    }

    private data class CliResult(val status: Int, val output: String)

    private fun runCli(args: Array<String>, temp: Path, resourceDir: Path? = null, omitResourcePath: String? = null): CliResult {
        val classpath = requireNotNull(System.getProperty("catalog.testClasspath"))
        val entries = classpath.split(java.io.File.pathSeparator)
        val retained = entries.filterNot { element ->
            if (omitResourcePath == null) false else {
                val path = Path.of(element)
                when {
                    Files.isDirectory(path) -> Files.isRegularFile(path.resolve(omitResourcePath))
                    Files.isRegularFile(path) && element.endsWith(".jar") ->
                        java.util.jar.JarFile(path.toFile()).use { it.getEntry(omitResourcePath) != null }
                    else -> false
                }
            }
        }
        if (omitResourcePath != null) require(retained.size < entries.size) { "Compiled resource not on test classpath: $omitResourcePath" }
        val baseClasspath = retained.joinToString(java.io.File.pathSeparator)
        val effectiveClasspath = if (resourceDir == null) baseClasspath else resourceDir.toString() + java.io.File.pathSeparator + baseClasspath
        val java = Path.of(System.getProperty("java.home"), "bin", "java").toString()
        val log = temp.resolve("child.log")
        val process = ProcessBuilder(listOf(java, "-Djava.awt.headless=true", "-cp", effectiveClasspath, "catalog.MainKt") + args.toList())
            .redirectErrorStream(true)
            .redirectOutput(log.toFile())
            .apply {
                environment().remove("DISPLAY")
                environment().remove("WAYLAND_DISPLAY")
                environment().remove("XAUTHORITY")
            }
            .start()
        try {
            assertTrue(process.waitFor(45, TimeUnit.SECONDS), "Headless CLI did not terminate within 45 seconds")
            return CliResult(process.exitValue(), Files.readString(log))
        } finally {
            if (process.isAlive) process.destroyForcibly().waitFor()
        }
    }

    private data class Fixture(val entry: JsonObject, val source: Path, val manifestPath: Path)

    private fun fixture(temp: Path): Fixture {
        val original = JsonParser.parseString(Files.readString(Path.of(requireNotNull(System.getProperty("catalog.generatedManifest"))))).asJsonObject
        val compiled = generatedCatalog().associateBy { it.id }
        val entry = original.getAsJsonArray("entries").map { it.asJsonObject }.first { candidate ->
            val icon = compiled[candidate.get("id").asInt]
            icon != null && icon.error == null && icon.resourceName != null &&
                icon.sourcePath == candidate.get("sourcePath").asString &&
                icon.xmlSha256 == candidate.get("xmlSha256").asString &&
                Path.of(icon.sourcePath).isAbsolute.not()
        }
        val sourcePath = Path.of(entry.get("sourcePath").asString)
        val source = temp.resolve(sourcePath)
        Files.createDirectories(source.parent)
        Files.copy(Path.of(original.get("sourceRoot").asString).resolve(sourcePath), source)
        val manifest = original.deepCopy().apply {
            addProperty("sourceRoot", temp.toString())
            addProperty("banksRoot", temp.resolve("banks").toString())
            add("entries", JsonArray().apply { add(entry.deepCopy()) })
        }
        val manifestPath = temp.resolve("manifest.json")
        Files.writeString(manifestPath, manifest.toString())
        return Fixture(entry, source, manifestPath)
    }

    private fun deleteTree(path: Path) {
        Files.walk(path).use { paths -> paths.sorted(Comparator.reverseOrder<Path>()).forEach(Files::deleteIfExists) }
    }

    private fun sha(path: Path): String = sha(Files.readAllBytes(path))

    private fun sha(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { "%02x".format(it) }
}