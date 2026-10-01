package catalog

import androidx.compose.ui.window.ComposeUIViewController
import platform.UIKit.UIViewController

fun MainViewController(): UIViewController = ComposeUIViewController {
    CatalogApp(generatedCatalog())
}

internal actual fun platformRenderer(): String =
    "Compose iOS / Skia painterResource (VectorDrawable XML)"
