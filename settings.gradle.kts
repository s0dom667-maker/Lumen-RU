// Modified 2026 by s0dom667-maker: pinned local engine dependency.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

// Build the exact engine pinned by the upstream release (prepare with tools/release/prepare-deps.sh).
// Do not silently substitute an unrelated sibling checkout or an older Maven artifact.
check(file(".deps/lumen-ai/settings.gradle.kts").isFile) {
    "Run tools/release/prepare-deps.sh first; see RELEASE.md"
}
includeBuild(".deps/lumen-ai") {
    dependencySubstitution {
        substitute(module("app.lumen-photos:lumen-ai")).using(project(":ai"))
    }
}

rootProject.name = "Lumen"
include(":app")
include(":baselineprofile")
