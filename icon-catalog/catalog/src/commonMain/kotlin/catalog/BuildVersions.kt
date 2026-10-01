package catalog

object BuildVersions {
    val renderer: String get() = platformRenderer()
    const val version = "Compose 1.12.0 · Kotlin 2.4.20 · sdk-common 32.4.0"
}

internal expect fun platformRenderer(): String
