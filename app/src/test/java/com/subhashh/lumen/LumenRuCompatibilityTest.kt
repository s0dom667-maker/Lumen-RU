package com.subhashh.lumen

import android.app.Application
import android.content.res.Configuration
import android.os.LocaleList
import androidx.core.content.FileProvider
import com.subhashh.lumen.data.Updater
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.io.File
import java.util.Locale

/** Checks the user-visible localization and file sharing under the independent app identity. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [35], application = Application::class)
class LumenRuCompatibilityTest {
    @Test
    fun russianResourcesAndPluralRulesWorkAtRuntime() {
        val application = RuntimeEnvironment.getApplication()
        val config = Configuration(application.resources.configuration).apply {
            setLocales(LocaleList(Locale.forLanguageTag("ru-RU")))
        }
        val resources = application.createConfigurationContext(config).resources
        assertEquals("Lumen RU", resources.getString(R.string.app_name))
        assertEquals("Настройки", resources.getString(R.string.common_settings))
        for ((count, expected) in listOf(1 to "1 объект", 2 to "2 объекта", 5 to "5 объектов", 21 to "21 объект", 22 to "22 объекта", 25 to "25 объектов")) {
            assertEquals(expected, resources.getQuantityString(R.plurals.main_item_count, count, count))
        }
    }

    @Test
    fun fileSharingUsesOurOwnAuthority() {
        val context = RuntimeEnvironment.getApplication()
        assertEquals("io.github.s0dom667maker.lumenru.debug", context.packageName)
        val file = File(context.cacheDir, "exports/share-test.jpg").apply { parentFile!!.mkdirs(); writeBytes(byteArrayOf(1)) }
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)
        assertEquals("${context.packageName}.files", uri.authority)
        assertNotEquals("com.subhashh.lumen.files", uri.authority)
    }

    @Test
    fun updaterFollowsOurReleasesAndUnderstandsOurVersions() {
        assertEquals("https://github.com/s0dom667-maker/Lumen-RU", Updater.RepoUrl)
        assertTrue(Updater.isNewer("1.0.1", "1.0.0"))
        assertTrue(Updater.isNewer("1.1.0", "1.0.9"))
        assertFalse(Updater.isNewer("1.0.0", "1.0.0"))
    }
}
