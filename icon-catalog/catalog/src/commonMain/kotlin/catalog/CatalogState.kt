package catalog

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

data class CatalogEntry(
    val id: Int,
    val title: String,
    val sourcePath: String,
    val sourceSha256: String? = null,
    val xmlSha256: String? = null,
    val sourceWidth: String? = null,
    val sourceHeight: String? = null,
    val viewBox: String? = null,
    val naturalWidth: Float? = null,
    val naturalHeight: Float? = null,
    val resourceName: String? = null,
    val error: String? = null,
    val warnings: List<String> = emptyList(),
)

class CatalogState(private val entries: List<CatalogEntry>) {
    private var currentQuery by mutableStateOf("")
    val query: String get() = currentQuery

    var selectedEntry by mutableStateOf<CatalogEntry?>(null)
        private set

    val filteredEntries: List<CatalogEntry>
        get() {
            val term = query.trim()
            if (term.isEmpty()) return entries
            return entries.filter { entry ->
                term in entry.id.toString() || entry.title.contains(term, ignoreCase = true)
            }
        }

    fun setQuery(value: String) {
        currentQuery = value
    }

    fun open(entry: CatalogEntry) {
        selectedEntry = entry
    }

    fun close() {
        selectedEntry = null
    }
}
