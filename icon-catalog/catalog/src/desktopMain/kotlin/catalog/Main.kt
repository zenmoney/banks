package catalog

import androidx.compose.runtime.remember
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.application
import androidx.compose.ui.window.rememberWindowState

fun main(args: Array<String>) {
    if (args.isNotEmpty()) {
        kotlin.system.exitProcess(runDesktopExport(args))
    }
    application {
        Window(onCloseRequest = ::exitApplication, title = "Bank icon catalog — read only", state = rememberWindowState(width = 1200.dp, height = 900.dp)) {
            CatalogApp(remember { generatedCatalog() })
        }
    }
}
