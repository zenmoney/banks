plugins {
    alias(libs.plugins.kotlin.multiplatform)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.compose.multiplatform)
    alias(libs.plugins.android.multiplatform.library)
}
kotlin {
    jvm("desktop") { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }
    android {
        namespace = "catalog.shared"
        compileSdk = 37
        minSdk = 26
        androidResources.enable = true
        withHostTest {}
        compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
    }
    listOf(iosArm64(), iosSimulatorArm64()).forEach { target ->
        target.binaries.framework {
            baseName = "IconCatalog"
            isStatic = true
        }
    }
    jvmToolchain(21)
    sourceSets {
        commonMain.dependencies {
            implementation(libs.compose.runtime)
            implementation(libs.compose.foundation)
            implementation(libs.compose.material3)
            implementation(libs.compose.resources)
        }
        commonTest.dependencies { implementation(libs.kotlin.test) }
        named("desktopMain") { dependencies {
            implementation(compose.desktop.currentOs)
            implementation(libs.gson)
        } }
    }
}
val generatedCatalogDir = layout.buildDirectory.dir("generated/catalog")
val generatorClasspath = configurations.create("generatorClasspath")
dependencies { add(generatorClasspath.name, project(":generator")) }
val generateCatalogResources = tasks.register<JavaExec>("generateCatalogResources") {
    group = "catalog"
    description = "Validate immutable SVG sources and generate VectorDrawable resources and provenance."
    classpath = generatorClasspath
    mainClass.set("catalog.generator.MainKt")
    val banks = providers.gradleProperty("catalogBanks").orElse(rootProject.layout.projectDirectory.dir("../banks").asFile.absolutePath)
    val bankId = providers.gradleProperty("catalogId")
    val candidate = providers.gradleProperty("catalogCandidate")
    inputs.dir(banks)
    inputs.property("bankId", bankId.orElse(""))
    inputs.property("candidate", candidate.orElse(""))
    if (candidate.isPresent) inputs.file(candidate)
    outputs.dir(generatedCatalogDir)
    doFirst {
        args = listOf("--banks", banks.get(), "--output", generatedCatalogDir.get().asFile.absolutePath) +
            (bankId.orNull?.let { listOf("--id", it) } ?: emptyList()) +
            (candidate.orNull?.let { listOf("--candidate", it) } ?: emptyList())
    }
}
kotlin.sourceSets.commonMain { kotlin.srcDir(generateCatalogResources.map { generatedCatalogDir.get().dir("kotlin") }) }
compose.resources {
    packageOfResClass = "catalog.resources"
    generateResClass = always
    customDirectory(sourceSetName = "commonMain", directoryProvider = generateCatalogResources.map { generatedCatalogDir.get().dir("composeResources") })
}
compose.desktop { application { mainClass = "catalog.MainKt" } }
tasks.named<Test>("desktopTest") {
    systemProperty("catalog.generatedManifest", generatedCatalogDir.get().file("manifest.json").asFile.absolutePath)
    doFirst { systemProperty("catalog.testClasspath", classpath.asPath) }
}
