package catalog.generator

import com.android.ide.common.vectordrawable.Svg2Vector
import com.google.gson.GsonBuilder
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.nio.file.Path
import java.security.MessageDigest
import javax.xml.XMLConstants
import javax.xml.parsers.DocumentBuilderFactory
import org.w3c.dom.Element
import org.w3c.dom.Node

private val gson = GsonBuilder().serializeNulls().disableHtmlEscaping().setPrettyPrinting().create()
private const val converterVersion = "com.android.tools:sdk-common:32.4.0"
private const val composeVersion = "1.12.0"
private val folderSuffix = Regex("_(-?\\d+)-([^_]+)$")
private val resourceFilePattern = Regex("bank_(?:m)?\\d+\\.xml")
private val resourceNamePattern = Regex("bank_(?:m)?\\d+")
private val numericLength = Regex("^([+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?)(?:px)?$")
private val sizedLength = Regex("^([+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?)[A-Za-z%]*$")
private val localUrl = Regex("url\\(\\s*(['\"]?)#[-\\w:.]+\\1\\s*\\)", RegexOption.IGNORE_CASE)
private val anyUrl = Regex("url\\([^)]*\\)", RegexOption.IGNORE_CASE)

/** The public, embeddable CLI boundary; main translates its status into a process exit code. */
fun run(args: Array<String>): Int {
    val options = try { parseArgs(args) } catch (e: IllegalArgumentException) {
        System.err.println(e.message)
        return 2
    }
    val banks = options.banks
    if (!Files.isDirectory(banks)) {
        System.err.println("Bank source directory does not exist: $banks")
        return 2
    }
    val output = options.output
    try {
        val banksReal = banks.toRealPath()
        val effectiveOutput = resolvedPath(output)
        require(!effectiveOutput.startsWith(banksReal)) { "Output must not be inside banks source directory" }
        require(options.candidate == null || !resolvedPath(options.candidate).startsWith(effectiveOutput)) { "Candidate must not be inside generated output" }
    } catch (e: Exception) {
        System.err.println("Unsafe output: ${e.message}")
        return 2
    }
    val directories = Files.list(banks).use { stream -> stream.filter { Files.isDirectory(it) }.sorted().toList() }
    val all = directories.filter { Files.isRegularFile(it.resolve("icon.svg")) ||
        (options.id != null && metadataId(it.resolve("info.json")) == options.id) }
        .map { loadBank(it, banks) }.toMutableList()
    val selected = if (options.id == null) all else all.filter { it.id == options.id }
    if (options.id != null && selected.isEmpty()) {
        System.err.println("Bank ID ${options.id} was not found")
        return 2
    }
    val duplicateIds = directories.mapNotNull { metadataId(it.resolve("info.json")) }.groupingBy { it }.eachCount().filterValues { it > 1 }.keys
    for (entry in all) if (entry.id in duplicateIds) entry.error = "Duplicate bank ID ${entry.id}"
    if (options.candidate != null) {
        for (entry in selected) {
            entry.source = options.candidate
            entry.sourcePath = options.candidate.toAbsolutePath().normalize().toString()
        }
    }
    val drawable = output.resolve("composeResources/drawable")
    try {
        val previousResources = previousResourceNames(output.resolve("manifest.json"))
        val ownedPaths = listOf(output, output.resolve("composeResources"), drawable, output.resolve("kotlin"), output.resolve("kotlin/catalog"), output.resolve("manifest.json"), output.resolve("kotlin/catalog/GeneratedCatalog.kt"))
        require(ownedPaths.none { Files.isSymbolicLink(it) }) { "Generated output must not contain symlinks" }
        if (Files.isDirectory(drawable)) Files.list(drawable).use { stream ->
            require(stream.noneMatch { it.fileName.toString().matches(resourceFilePattern) && Files.isSymbolicLink(it) }) { "Generated output must not contain symlink resources" }
        }
        for (entry in selected) if (entry.id != null && entry.error == null) {
            val name = resourceName(entry.id)
            require(name in previousResources || !Files.exists(drawable.resolve("$name.xml"))) { "Refusing to overwrite non-generated resource $name" }
        }
        Files.createDirectories(drawable)
        for (entry in selected) {
            if (entry.error == null) convert(entry, drawable)
        }
        // Only names attributed to the previous manifest belong to this generator.
        for (name in previousResources) if (selected.none { it.resourceName == name })
            Files.deleteIfExists(drawable.resolve("$name.xml"))
        val manifest = JsonObject().apply {
            addProperty("schemaVersion", 1)
            addProperty("banksRoot", banks.toString())
            addProperty("sourceRoot", banks.parent.toString())
            add("pins", JsonObject().apply {
                addProperty("converter", converterVersion)
                addProperty("compose", composeVersion)
            })
            add("entries", gson.toJsonTree(selected.sortedWith(compareBy<Bank> { it.id ?: Int.MAX_VALUE }.thenBy { it.sourcePath })))
        }
        Files.createDirectories(output.resolve("kotlin/catalog"))
        Files.writeString(output.resolve("manifest.json"), gson.toJson(manifest) + "\n")
        Files.writeString(output.resolve("kotlin/catalog/GeneratedCatalog.kt"), generateKotlin(selected.sortedWith(compareBy<Bank> { it.id ?: Int.MAX_VALUE }.thenBy { it.sourcePath })))
    } catch (e: Exception) {
        System.err.println("Failed to write generated catalog: ${e.message}")
        return 2
    }
    selected.filter { it.error != null }.forEach { System.err.println("${it.sourcePath}: ${it.error}") }
    return if (options.id != null && selected.any { it.error != null }) 1 else 0
}

fun main(args: Array<String>) { kotlin.system.exitProcess(run(args)) }

private fun resourceName(id: Int): String = if (id < 0) "bank_m${-id.toLong()}" else "bank_$id"
private fun resolvedPath(path: Path): Path {
    val absolute = path.toAbsolutePath().normalize()
    val ancestor = generateSequence(absolute) { it.parent }.first { Files.exists(it) }
    return ancestor.toRealPath().resolve(ancestor.relativize(absolute)).normalize()
}
private fun previousResourceNames(manifest: Path): Set<String> = try {
    JsonParser.parseString(Files.readString(manifest)).asJsonObject.getAsJsonArray("entries")
        .mapNotNull { row -> row.asJsonObject.get("resourceName")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString }
        .filter { it.matches(resourceNamePattern) }.toSet()
} catch (_: Exception) { emptySet() }
private data class Options(val banks: Path, val output: Path, val id: Int?, val candidate: Path?)
private fun parseArgs(args: Array<String>): Options {
    require(args.size % 2 == 0) { "Expected --banks PATH --output PATH [--id INT] [--candidate PATH]" }
    val values = mutableMapOf<String, String>()
    for (i in args.indices step 2) {
        require(args[i] in setOf("--banks", "--output", "--id", "--candidate") && values.putIfAbsent(args[i], args[i + 1]) == null) { "Unknown or duplicate option ${args[i]}" }
    }
    val banks = values["--banks"] ?: throw IllegalArgumentException("Missing --banks")
    val output = values["--output"] ?: throw IllegalArgumentException("Missing --output")
    val id = values["--id"]?.toIntOrNull()
    require(values["--id"] == null || id != null) { "--id must be an integer" }
    require(values["--candidate"] == null || id != null) { "--candidate requires --id" }
    return Options(Path.of(banks).toAbsolutePath().normalize(), Path.of(output).toAbsolutePath().normalize(), id, values["--candidate"]?.let { Path.of(it) })
}

private data class Bank(
    val id: Int?, val title: String, var sourcePath: String, @Transient var source: Path,
    var sourceSha256: String? = null, var sourceValidated: Boolean = false, var xmlSha256: String? = null,
    var sourceWidth: String? = null, var sourceHeight: String? = null, var viewBox: String? = null,
    var naturalWidth: Float? = null, var naturalHeight: Float? = null,
    var resourceName: String? = null, var error: String? = null,
    val warnings: MutableList<String> = mutableListOf()
)

private fun metadataId(path: Path): Int? = try {
    JsonParser.parseString(Files.readString(path)).asJsonObject.get("id")?.asString?.toIntOrNull()
} catch (_: Exception) { null }

private fun loadBank(directory: Path, banks: Path): Bank {
    val icon = directory.resolve("icon.svg")
    val sourcePath = "${banks.fileName}/${directory.fileName}/icon.svg"
    val info = try { JsonParser.parseString(Files.readString(directory.resolve("info.json"))).asJsonObject }
        catch (e: Exception) { return Bank(null, directory.fileName.toString(), sourcePath, icon, error = "Missing or invalid info.json: ${e.message}") }
    val idString = info.get("id")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asString
    val id = idString?.takeIf { it.matches(Regex("-?\\d+")) }?.toIntOrNull()
    val title = info.get("title")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString
    val bank = Bank(id, title ?: directory.fileName.toString(), sourcePath, icon)
    if (id == null || title.isNullOrBlank()) bank.error = "info.json requires integer id and nonempty title"
    val suffix = folderSuffix.find(directory.fileName.toString())
    if (id != null && suffix != null && suffix.groupValues[1].toIntOrNull() != id) bank.error = "Folder ID differs from info.json ID $id"
    val countryValue = info.get("countryCode")?.takeUnless { it.isJsonNull }
    val country = countryValue?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString
    if (countryValue != null && country == null) bank.error = "info.json countryCode must be a string or null"
    if (country != null && suffix != null && !suffix.groupValues[2].equals(country, ignoreCase = true)) bank.error = "Folder country differs from info.json countryCode $country"
    if (suffix != null && title != null && directory.fileName.toString().removeSuffix(suffix.value) != title)
        bank.warnings += "Folder title differs from info.json title"
    if (!Files.isRegularFile(icon)) bank.error = "Missing icon.svg"
    return bank
}

private fun convert(bank: Bank, drawable: Path) {
    try {
        val bytes = Files.readAllBytes(bank.source)
        bank.sourceSha256 = sha(bytes)
        val root = validateSvg(bytes)
        bank.sourceWidth = root.getAttribute("width").ifBlank { null }
        bank.sourceHeight = root.getAttribute("height").ifBlank { null }
        bank.viewBox = root.getAttribute("viewBox").ifBlank { null }
        validateLength(bank.sourceWidth, "width")
        validateLength(bank.sourceHeight, "height")
        bank.sourceValidated = true
        val width = natural(bank.sourceWidth)
        val height = natural(bank.sourceHeight)
        if (width != null && height != null) {
            bank.naturalWidth = width
            bank.naturalHeight = height
        }
        val input = Files.createTempFile("catalog-icon-", ".svg")
        val xml = try {
            Files.write(input, bytes)
            val output = ByteArrayOutputStream()
            val diagnostics = Svg2Vector.parseSvgToXml(input, output)
            if (!diagnostics.isNullOrBlank()) throw IllegalArgumentException("Svg2Vector: $diagnostics")
            output.toByteArray().also { require(it.isNotEmpty()) { "Svg2Vector emitted no drawable" } }
        } finally { Files.deleteIfExists(input) }
        val name = resourceName(requireNotNull(bank.id))
        Files.write(drawable.resolve("$name.xml"), xml)
        bank.resourceName = name
        bank.xmlSha256 = sha(xml)
    } catch (e: Exception) {
        bank.error = e.message ?: e.javaClass.simpleName
        if (bank.id != null) Files.deleteIfExists(drawable.resolve("${resourceName(bank.id)}.xml"))
    }
}

private fun validateSvg(bytes: ByteArray): Element {
    val factory = DocumentBuilderFactory.newInstance().apply {
        isNamespaceAware = true
        setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
        setFeature("http://xml.org/sax/features/external-general-entities", false)
        setFeature("http://xml.org/sax/features/external-parameter-entities", false)
        setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false)
        setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true)
        setXIncludeAware(false)
        isExpandEntityReferences = false
        setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "")
        setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "")
    }
    val doc = factory.newDocumentBuilder().apply { setErrorHandler(object : org.xml.sax.helpers.DefaultHandler() {
        override fun fatalError(e: org.xml.sax.SAXParseException) { throw e }
        override fun error(e: org.xml.sax.SAXParseException) { throw e }
    }) }.parse(bytes.inputStream())
    val root = doc.documentElement
    require(root.localName == "svg" && root.namespaceURI == "http://www.w3.org/2000/svg") { "Expected SVG root" }
    fun inspect(node: Node) {
        if (node is Element) {
            val name = node.localName.lowercase()
            require(!name.startsWith("animate") && name !in setOf("script", "foreignobject", "image", "set", "discard", "mpath")) { "Unsafe SVG element <$name>" }
            for (i in 0 until node.attributes.length) {
                val attribute = node.attributes.item(i)
                val key = attribute.localName.lowercase()
                val value = attribute.nodeValue
                require(!key.startsWith("on") && key != "base") { "Unsafe SVG attribute ${attribute.nodeName}" }
                if (key == "href") require(value.startsWith("#") && value.length > 1) { "External SVG link" }
                require(!value.contains("data:", ignoreCase = true) && !value.contains("@import", ignoreCase = true)) { "Embedded or external SVG content" }
                val withoutLocalUrls = localUrl.replace(value, "")
                require(!withoutLocalUrls.contains("url(", ignoreCase = true) && !anyUrl.containsMatchIn(withoutLocalUrls)) { "External or malformed SVG URL" }
            }
        }
        if (node.nodeType == Node.TEXT_NODE || node.nodeType == Node.CDATA_SECTION_NODE) {
            val text = node.nodeValue
            require(!text.contains("@import", ignoreCase = true) && !text.contains("data:", ignoreCase = true)) { "Embedded or external SVG content" }
            require(!localUrl.replace(text, "").contains("url(", ignoreCase = true)) { "External SVG URL" }
        }
        for (i in 0 until node.childNodes.length) inspect(node.childNodes.item(i))
    }
    inspect(root)
    return root
}

private fun natural(raw: String?): Float? {
    val numeric = raw?.let { numericLength.matchEntire(it.trim()) }?.groupValues?.get(1)?.toFloatOrNull()
    return numeric?.takeIf { it.isFinite() && it > 0f }
}
private fun validateLength(raw: String?, name: String) {
    if (raw == null) return
    val value = raw.trim()
    val number = sizedLength.matchEntire(value)?.groupValues?.get(1)?.toDoubleOrNull()
    require(number?.let { it.isFinite() && it > 0.0 } ?: !value.matches(Regex("[+-]?(?:NaN|Infinity)(?:px)?", RegexOption.IGNORE_CASE))) { "Invalid SVG $name: $raw" }
}
private fun sha(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

private fun literal(value: String?): String = value?.let { gson.toJson(it).replace("$", "\\$") } ?: "null"
private fun generatedEntry(bank: Bank): String = "CatalogEntry(id = ${bank.id ?: -1}, title = ${literal(bank.title)}, sourcePath = ${literal(bank.sourcePath)}, sourceSha256 = ${literal(bank.sourceSha256)}, xmlSha256 = ${literal(bank.xmlSha256)}, sourceWidth = ${literal(bank.sourceWidth)}, sourceHeight = ${literal(bank.sourceHeight)}, viewBox = ${literal(bank.viewBox)}, naturalWidth = ${bank.naturalWidth?.let { "${it}f" } ?: "null"}, naturalHeight = ${bank.naturalHeight?.let { "${it}f" } ?: "null"}, resourceName = ${literal(bank.resourceName)}, error = ${literal(bank.error)}, warnings = ${if (bank.warnings.isEmpty()) "emptyList()" else bank.warnings.joinToString(", ", "listOf(", ")") { literal(it) }})"
private fun generateKotlin(entries: List<Bank>): String = buildString {
    appendLine("package catalog")
    appendLine()
    appendLine("fun generatedCatalog(): List<CatalogEntry> = buildList {")
    for (index in entries.indices step 100) appendLine("    generatedCatalog${index / 100}(this)")
    appendLine("}")
    entries.chunked(100).forEachIndexed { index, chunk ->
        appendLine("private fun generatedCatalog$index(target: MutableList<CatalogEntry>) {")
        for (bank in chunk) appendLine("    target.add(${generatedEntry(bank)})")
        appendLine("}")
    }
}
