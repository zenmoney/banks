plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "catalog.android"
    compileSdk = 37
    defaultConfig {
        applicationId = "catalog.android"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
}
kotlin {
    jvmToolchain(21)
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

dependencies {
    implementation(project(":catalog"))
    implementation(libs.androidx.activity.compose)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material3)
}

// AGP requires a DirectoryProperty to track a generated source directory and its producer.
abstract class CatalogResourceSync : Sync() {
    @get:OutputDirectory
    abstract val outputDirectory: DirectoryProperty
    init { into(outputDirectory) }
}

// Native Android receives the exact XML produced for Compose, never a second conversion.
val generatedCatalog = project(":catalog").layout.buildDirectory.dir("generated/catalog")
val stageNativeDrawables = tasks.register<CatalogResourceSync>("stageNativeDrawables") {
    dependsOn(":catalog:generateCatalogResources")
    from(generatedCatalog.map { it.dir("composeResources/drawable") }) { into("drawable") }
    outputDirectory.set(layout.buildDirectory.dir("generated/native-res"))
}
val stageCatalogManifest = tasks.register<CatalogResourceSync>("stageCatalogManifest") {
    dependsOn(":catalog:generateCatalogResources")
    from(generatedCatalog.map { it.file("manifest.json") })
    outputDirectory.set(layout.buildDirectory.dir("generated/catalog-assets"))
}
androidComponents.onVariants { variant ->
    variant.sources.res?.addGeneratedSourceDirectory(stageNativeDrawables) { it.outputDirectory }
    variant.sources.assets?.addGeneratedSourceDirectory(stageCatalogManifest) { it.outputDirectory }
}
