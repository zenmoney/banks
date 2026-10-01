package catalog

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun CatalogApp(entries: List<CatalogEntry>) {
    val state = remember(entries) { CatalogState(entries) }
    val drawableValidation = remember(entries) { mutableStateMapOf<DrawableCacheKey, DrawableReadiness>() }
    val observedDistortions = remember { mutableStateMapOf<String, Boolean>() }
    var darkTheme by remember { mutableStateOf(false) }

    MaterialTheme(colorScheme = if (darkTheme) darkColorScheme() else lightColorScheme()) {
        Surface(Modifier.fillMaxSize()) {
            val selected = state.selectedEntry
            if (selected == null) {
                Column(Modifier.fillMaxSize().padding(16.dp)) {
                    Text("Bank icon catalog", style = MaterialTheme.typography.headlineMedium)
                    RendererLabel()
                    Button(onClick = { darkTheme = !darkTheme }) {
                        Text(if (darkTheme) "Use light background" else "Use dark background")
                    }
                    OutlinedTextField(
                        value = state.query,
                        onValueChange = state::setQuery,
                        label = { Text("Search ID or bank name") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                    )
                    Spacer(Modifier.height(12.dp))
                    LazyVerticalGrid(
                        columns = GridCells.Adaptive(minSize = 112.dp),
                        contentPadding = PaddingValues(bottom = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        items(state.filteredEntries) { entry ->
                            Card(onClick = { state.open(entry) }) {
                                Column(
                                    Modifier.fillMaxWidth().padding(8.dp),
                                    horizontalAlignment = Alignment.CenterHorizontally,
                                ) {
                                    Box(Modifier.size(54.dp), contentAlignment = Alignment.Center) {
                                        CatalogIcon(entry, Modifier.size(54.dp), drawableValidation)
                                    }
                                    Text(entry.title, style = MaterialTheme.typography.bodyMedium)
                                    Text("ID ${entry.id}", style = MaterialTheme.typography.bodySmall)
                                }
                            }
                        }
                    }
                }
            } else {
                Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
                    Button(onClick = state::close) { Text("Back to results") }
                    Text(selected.title, style = MaterialTheme.typography.headlineMedium)
                    Text("ID ${selected.id}")
                    RendererLabel()
                    Button(onClick = { darkTheme = !darkTheme }) {
                        Text(if (darkTheme) "Use light background" else "Use dark background")
                    }
                    Spacer(Modifier.height(12.dp))
                    Text("Generated VectorDrawable at native size", style = MaterialTheme.typography.titleMedium)
                    Box(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
                        CatalogIcon(selected, Modifier, drawableValidation, nativeSize = true)
                    }
                    Spacer(Modifier.height(12.dp))
                    MetadataLine("Source", selected.sourcePath)
                    MetadataLine("SVG width", selected.sourceWidth ?: "not specified")
                    MetadataLine("SVG height", selected.sourceHeight ?: "not specified")
                    MetadataLine("SVG viewBox", selected.viewBox ?: "not specified")
                    MetadataLine("Source SHA-256", selected.sourceSha256 ?: "not available")
                    MetadataLine("Generated XML SHA-256", selected.xmlSha256 ?: "not available")
                    selected.error?.let { MetadataLine("Conversion error", it) }
                    selected.warnings.forEach { MetadataLine("Conversion warning", it) }
                    val version = selected.sourceSha256?.let { source ->
                        selected.xmlSha256?.let { xml -> "${selected.id}:$source:$xml" }
                    }
                    if (version != null && selected.error == null && selected.resourceName != null) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Checkbox(
                                checked = observedDistortions[version] == true,
                                onCheckedChange = { observedDistortions[version] = it },
                            )
                            Text(
                                "Observed distortion in this exact version (local session only)",
                                modifier = Modifier.clickable {
                                    observedDistortions[version] = observedDistortions[version] != true
                                },
                            )
                        }
                        if (observedDistortions[version] == true) {
                            Text(
                                "OBSERVED DISTORTION — not a verification; local note for SVG ${selected.sourceSha256} / XML ${selected.xmlSha256}",
                                color = MaterialTheme.colorScheme.error,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun RendererLabel() {
    Text("Renderer: ${BuildVersions.renderer}", style = MaterialTheme.typography.bodySmall)
    Text("Version: ${BuildVersions.version}", style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun MetadataLine(label: String, value: String) {
    Text("$label: $value", style = MaterialTheme.typography.bodyMedium)
}
