package catalog

import android.os.Build

internal actual fun platformRenderer(): String =
    "Compose Android / HWUI · API ${Build.VERSION.SDK_INT} (${Build.VERSION.RELEASE}) · generated VectorDrawable XML · painterResource"
