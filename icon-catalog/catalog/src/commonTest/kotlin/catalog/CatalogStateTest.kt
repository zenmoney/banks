package catalog

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class CatalogStateTest {
    private val entries = listOf(
        CatalogEntry(id = 42, title = "North Bank", sourcePath = "icons/north.svg"),
        CatalogEntry(id = 317, title = "Southern Credit", sourcePath = "icons/south.svg"),
        CatalogEntry(id = 8, title = "Coastal Bank", sourcePath = "icons/coastal.svg"),
    )

    @Test
    fun searchMatchesIdAndTitleSubstringsIgnoringCase() {
        val state = CatalogState(entries)

        state.setQuery("  bAnK  ")
        assertEquals(listOf(42, 8), state.filteredEntries.map { it.id })

        state.setQuery("17")
        assertEquals(listOf(317), state.filteredEntries.map { it.id })
    }

    @Test
    fun returningFromDetailKeepsSearchAndResults() {
        val state = CatalogState(entries)
        state.setQuery("Bank")
        state.open(entries[2])

        assertEquals(8, state.selectedEntry?.id)
        state.close()

        assertNull(state.selectedEntry)
        assertEquals("Bank", state.query)
        assertEquals(listOf(42, 8), state.filteredEntries.map { it.id })
    }
}
