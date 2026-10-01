@file:OptIn(org.jetbrains.compose.resources.ExperimentalResourceApi::class)

package catalog

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.ImageComposeScene
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import catalog.resources.Res
import catalog.resources.allDrawableResources
import com.google.gson.GsonBuilder
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.jetbrains.compose.resources.LocalResourceReader
import org.jetbrains.compose.resources.ResourceReader
import org.jetbrains.skia.EncodedImageFormat
import org.jetbrains.skiko.Version
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.security.MessageDigest
import java.time.Instant
import java.util.concurrent.Callable
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicReference

private const val CANVAS_PX = 240
private const val ICON_DP = 54
private const val RENDER_TIMEOUT_SECONDS = 30L
private const val COMPOSE_PIN = "1.12.0"
private const val CONVERTER_PIN = "com.android.tools:sdk-common:32.4.0"
private val gson = GsonBuilder().disableHtmlEscaping().setPrettyPrinting().create()
private val versionedJar = Regex("-(\\d+\\.\\d+\\.\\d+)\\.jar$")
private val hexDigits = "0123456789abcdef".toCharArray()

/** Public one-shot CLI boundary. No successful result is published until both renders complete. */
fun runDesktopExport(args: Array<String>): Int {
    return try {
        val options = parseExportOptions(args)
        val entry = generatedCatalog().singleOrNull { it.id == options.id }
            ?: throw IllegalArgumentException("Bank ID ${options.id} was not found uniquely in compiled catalog")
        val input = bindInput(options, entry)
        val composeVersion = actualComposeVersion()
        require(composeVersion == COMPOSE_PIN) { "Loaded Compose resources version differs from manifest/build pin" }
        val executor = Executors.newSingleThreadExecutor { task ->
            Thread(task, "catalog-offscreen-export").apply { isDaemon = true }
        }
        val rendered = try {
            val job = executor.submit(Callable { renderBoth(input) })
            try {
                job.get(RENDER_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            } catch (timeout: TimeoutException) {
                job.cancel(true)
                throw IllegalStateException("Offscreen render did not finish within ${RENDER_TIMEOUT_SECONDS}s", timeout)
            } catch (failed: ExecutionException) {
                throw failed.cause ?: failed
            }
        } finally {
            executor.shutdownNow()
        }
        require(sha(Files.readAllBytes(input.source)) == input.sourceSha256) { "Source SVG changed during render" }
        require(sha(Files.readAllBytes(options.manifest)) == input.manifestSha256) { "Manifest changed during render" }
        publish(options.output, input, rendered, composeVersion)
        0
    } catch (failure: Exception) {
        System.err.println("Desktop export failed: ${failure.message ?: failure.javaClass.simpleName}")
        2
    }
}

private data class ExportOptions(val id: Int, val manifest: Path, val output: Path)
private data class BoundInput(
    val entry: CatalogEntry,
    val source: Path,
    val sourceSha256: String,
    val xmlSha256: String,
    val resourceName: String,
    val manifest: Path,
    val manifestSha256: String,
    val pins: JsonObject,
)
private data class Rendered(val light: ByteArray, val dark: ByteArray, val loadedSha256: String)

private fun parseExportOptions(args: Array<String>): ExportOptions {
    require(args.firstOrNull() == "export" && args.size == 7) {
        "Expected export --id INT --manifest ABS_JSON --output ABS_NEW_DIR"
    }
    val values = mutableMapOf<String, String>()
    for (index in 1 until args.size step 2) {
        require(args[index] in setOf("--id", "--manifest", "--output") && values.putIfAbsent(args[index], args[index + 1]) == null) {
            "Unknown or duplicate option ${args[index]}"
        }
    }
    val id = values.getValue("--id").toIntOrNull() ?: throw IllegalArgumentException("--id must be an integer")
    val manifest = Path.of(values.getValue("--manifest"))
    val output = Path.of(values.getValue("--output"))
    require(manifest.isAbsolute && output.isAbsolute) { "Manifest and output paths must be absolute" }
    require(Files.isRegularFile(manifest)) { "Manifest does not exist: $manifest" }
    require(!Files.exists(output)) { "Output must be a fresh directory: $output" }
    return ExportOptions(id, manifest, output)
}

private fun bindInput(options: ExportOptions, entry: CatalogEntry): BoundInput {
    val manifestBytes = Files.readAllBytes(options.manifest)
    val manifest = JsonParser.parseString(String(manifestBytes, Charsets.UTF_8)).asJsonObject
    require(manifest.get("schemaVersion")?.asInt == 1) { "Unsupported manifest schema" }
    val pins = manifest.getAsJsonObject("pins") ?: throw IllegalArgumentException("Manifest pins missing")
    require(pins.get("compose")?.asString == COMPOSE_PIN && pins.get("converter")?.asString == CONVERTER_PIN) {
        "Manifest converter/Compose pins differ from compiled catalog"
    }
    val matches = manifest.getAsJsonArray("entries")?.map { it.asJsonObject }?.filter { it.get("id")?.asInt == options.id }
        ?: throw IllegalArgumentException("Manifest entries missing")
    require(matches.size == 1) { "Bank ID ${options.id} must occur exactly once in manifest" }
    val row = matches.single()
    require(row.get("error")?.isJsonNull == true && entry.error == null && row.get("sourceValidated")?.asBoolean == true) {
        "Bank ID ${options.id} has SVG conversion/validation failure: ${row.get("error") ?: entry.error}"
    }
    val resourceName = row.get("resourceName")?.asString
    val sourceSha256 = row.get("sourceSha256")?.asString
    val xmlSha256 = row.get("xmlSha256")?.asString
    require(resourceName != null && sourceSha256 != null && xmlSha256 != null) { "Generated drawable or hashes missing" }
    require(row.get("title")?.asString == entry.title && row.get("sourcePath")?.asString == entry.sourcePath &&
        sourceSha256 == entry.sourceSha256 && xmlSha256 == entry.xmlSha256 && resourceName == entry.resourceName) {
        "Manifest entry for ID ${options.id} differs from compiled catalog entry"
    }
    require(Res.allDrawableResources.containsKey(resourceName)) { "Compiled drawable $resourceName is unavailable" }
    val root = Path.of(manifest.get("sourceRoot")?.asString ?: throw IllegalArgumentException("Manifest sourceRoot missing"))
    require(root.isAbsolute) { "Manifest sourceRoot must be absolute" }
    val source = root.resolve(entry.sourcePath).normalize()
    require(Files.isRegularFile(source)) { "SVG source unavailable: $source" }
    require(sha(Files.readAllBytes(source)) == sourceSha256) { "SVG source hash differs from manifest/compiled entry: $source" }
    return BoundInput(entry, source, sourceSha256, xmlSha256, resourceName, options.manifest, sha(manifestBytes), pins)
}

private fun renderBoth(input: BoundInput): Rendered {
    val readerRef = AtomicReference<ObservedPainterReader?>(null)
    val light = renderTheme(input.entry, input.resourceName, readerRef, Color.White)
    val reader = readerRef.get() ?: throw IllegalStateException("Painter resource reader did not initialize")
    val loaded = reader.loadedSha256 ?: throw IllegalStateException("painterResource did not read ${input.resourceName} XML (cached/absent resource)")
    require(loaded == input.xmlSha256) { "Actually loaded painter XML hash $loaded differs from manifest/compiled ${input.xmlSha256}" }
    val dark = renderTheme(input.entry, input.resourceName, readerRef, Color(0xFF121212))
    return Rendered(light, dark, loaded)
}

private fun renderTheme(entry: CatalogEntry, resourceName: String, readerRef: AtomicReference<ObservedPainterReader?>, background: Color): ByteArray {
    val readiness = AtomicReference<DrawableReadiness>(DrawableReadiness.Loading)
    val validation = mutableStateMapOf<DrawableCacheKey, DrawableReadiness>()
    val scene = ImageComposeScene(CANVAS_PX, CANVAS_PX, Density(3f), content = {
        val delegate = LocalResourceReader.current
        val reader = requireNotNull(readerRef.updateAndGet { it ?: ObservedPainterReader(delegate, resourceName) })
        CompositionLocalProvider(LocalResourceReader provides reader) {
            Box(Modifier.fillMaxSize().background(background), contentAlignment = Alignment.Center) {
                CatalogIcon(entry, Modifier.size(ICON_DP.dp), validation, onReadiness = { readiness.set(it) })
            }
        }
    })
    try {
        // Ready is emitted after the painter Image commits. Capture a subsequent frame,
        // not one whose pixels might still contain the prior Loading placeholder.
        repeat(100) {
            val readyBeforeFrame = readiness.get() == DrawableReadiness.Ready
            scene.render().use { frame ->
                when (val state = readiness.get()) {
                    DrawableReadiness.Ready -> if (readyBeforeFrame) {
                        return frame.encodeToData(EncodedImageFormat.PNG)?.use { it.bytes }
                            ?: throw IllegalStateException("Skia PNG encoding failed")
                    }
                    is DrawableReadiness.Failed -> throw IllegalStateException("Painter loading failed: ${state.message}")
                    DrawableReadiness.Loading -> Unit
                }
            }
            Thread.sleep(10)
        }
        throw IllegalStateException("Painter readiness did not resolve")
    } finally {
        scene.close()
    }
}

/** Witnesses the bytes supplied to painterResource, not the independent validation read. */
private class ObservedPainterReader(private val delegate: ResourceReader, name: String) : ResourceReader {
    private val expectedPath = "composeResources/catalog.resources/drawable/$name.xml"
    @Volatile var loadedSha256: String? = null
        private set

    override suspend fun read(path: String): ByteArray = delegate.read(path).also { bytes ->
        if (path == expectedPath) {
            val digest = sha(bytes)
            require(loadedSha256 == null || loadedSha256 == digest) { "Painter XML changed between theme reads" }
            loadedSha256 = digest
        }
    }

    override suspend fun readPart(path: String, offset: Long, size: Long): ByteArray = delegate.readPart(path, offset, size)
    override fun getUri(path: String): String = delegate.getUri(path)
}

private fun publish(output: Path, input: BoundInput, rendered: Rendered, composeVersion: String) {
    // Reserve the caller's fresh directory with CREATE_NEW semantics. Existing paths are
    // never overwritten; result.json is the last, complete-success marker. On write failure,
    // only this newly reserved directory may contain partial images without a result marker.
    Files.createDirectory(output)
    val images = listOf(
        mapOf("theme" to "light", "path" to "light.png", "sha256" to sha(rendered.light), "background" to "#FFFFFF"),
        mapOf("theme" to "dark", "path" to "dark.png", "sha256" to sha(rendered.dark), "background" to "#121212"),
    )
    Files.write(output.resolve("light.png"), rendered.light, StandardOpenOption.CREATE_NEW)
    Files.write(output.resolve("dark.png"), rendered.dark, StandardOpenOption.CREATE_NEW)
    val result = mapOf(
        "schemaVersion" to 1,
        "status" to "success",
        "id" to input.entry.id,
        "source" to mapOf("path" to input.source.toString(), "sha256" to input.sourceSha256),
        "xml" to mapOf("resourceName" to input.resourceName, "sha256" to input.xmlSha256, "loadedSha256" to rendered.loadedSha256),
        "manifest" to mapOf("path" to input.manifest.toString(), "sha256" to input.manifestSha256),
        "renderer" to "offscreen CPU Skia/painterResource",
        "pins" to input.pins,
        "runtime" to mapOf("compose" to composeVersion, "skiko" to Version.skiko, "skia" to Version.skia,
            "javaVersion" to System.getProperty("java.version"), "osName" to System.getProperty("os.name"), "osArch" to System.getProperty("os.arch")),
        "density" to 3,
        "sizePx" to CANVAS_PX,
        "iconSizeDp" to ICON_DP,
        "observedAt" to Instant.now().toString(),
        "images" to images,
    )
    val marker = Files.createTempFile(output, ".result-", ".tmp")
    try {
        Files.writeString(marker, gson.toJson(result) + "\n", StandardOpenOption.TRUNCATE_EXISTING)
        Files.move(marker, output.resolve("result.json"))
    } finally {
        Files.deleteIfExists(marker)
    }
}

private fun actualComposeVersion(): String {
    val type = ResourceReader::class.java
    val location = type.protectionDomain.codeSource?.location
        ?: throw IllegalStateException("Compose resource runtime artifact location unavailable")
    val filename = Path.of(location.toURI()).fileName.toString()
    return versionedJar.find(filename)?.groupValues?.get(1)
        ?: type.`package`.implementationVersion
        ?: throw IllegalStateException("Compose resource runtime version unavailable from $filename")
}

private fun sha(bytes: ByteArray): String {
    val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
    val chars = CharArray(digest.size * 2)
    for (index in digest.indices) {
        val octet = digest[index].toInt() and 0xff
        chars[index * 2] = hexDigits[octet ushr 4]
        chars[index * 2 + 1] = hexDigits[octet and 15]
    }
    return String(chars)
}
