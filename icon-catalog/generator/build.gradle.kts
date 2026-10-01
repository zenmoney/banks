plugins { alias(libs.plugins.kotlin.jvm); application }
kotlin {
    jvmToolchain(21)
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
    sourceSets.test { kotlin.srcDir("tests") }
}
tasks.withType<JavaCompile>().configureEach { options.release.set(17) }
dependencies {
    implementation(libs.android.sdk.common)
    implementation(libs.gson)
    testImplementation(libs.kotlin.test.junit5)
    testRuntimeOnly(libs.junit.platform.launcher)
}
application { mainClass.set("catalog.generator.MainKt") }
tasks.test { useJUnitPlatform() }
