// NativeLoader.kt
// Package and class name MUST match decrypt_loader.cpp's JNI export exactly:
//   Java_com_android_pictach_loader_NativeLoader_decryptDex
// If you rename this class or move its package, you must also rename
// the matching JNI function name in decrypt_loader.cpp, or the link
// will fail at runtime with UnsatisfiedLinkError.

package com.android.pictach.loader

import android.content.Context
import android.os.Build
import dalvik.system.InMemoryDexClassLoader
import java.io.IOException
import java.nio.ByteBuffer

class NativeLoader(private val context: Context) {

    companion object {
        init {
            // Must match CMakeLists.txt's add_library(decryptloader ...) name
            System.loadLibrary("decryptloader")
        }
    }

    // Native method — implemented in decrypt_loader.cpp.
    // Takes the raw encrypted bytes (read from assets/logic.bin by
    // readEncryptedBlob() below) and returns decrypted dex bytes, or
    // null if decryption/verification failed.
    private external fun decryptDex(encryptedBlob: ByteArray): ByteArray?

    /**
     * Reads assets/logic.bin, decrypts it natively, and returns a
     * ClassLoader containing all the classes that were extracted at
     * build time. Returns null if anything in the chain fails — the
     * caller is responsible for deciding how to handle that (e.g.
     * show an error, decline to start a feature, etc.) rather than
     * this function crashing the app outright.
     */
    fun loadExtractedClasses(): ClassLoader? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            // InMemoryDexClassLoader requires API 26+. Below that,
            // you'd need the older DexClassLoader + temp file approach
            // instead — not implemented here.
            return null
        }

        val encryptedBlob = readEncryptedBlob() ?: return null
        val decryptedDexBytes = decryptDex(encryptedBlob) ?: return null

        return try {
            InMemoryDexClassLoader(
                ByteBuffer.wrap(decryptedDexBytes),
                context.classLoader
            )
        } catch (e: Exception) {
            // Catches dex format errors, verification failures, etc.
            // Deliberately broad here since many different things can
            // go wrong turning raw bytes into a usable ClassLoader,
            // and the caller only needs to know "it didn't work."
            null
        }
    }

    private fun readEncryptedBlob(): ByteArray? {
        return try {
            context.assets.open("logic.bin").use { it.readBytes() }
        } catch (e: IOException) {
            null
        }
    }
}

/*
 * Example usage from one of your KEEP classes (e.g. MainActivity):
 *
 *   val loader = NativeLoader(this).loadExtractedClasses()
 *   if (loader == null) {
 *       // handle failure — e.g. show an error, disable a feature
 *       return
 *   }
 *   val cls = loader.loadClass("com.android.pictach.SomeExtractedClass")
 *   val instance = cls.getDeclaredConstructor().newInstance()
 *   val method = cls.getMethod("someMethodName")
 *   val result = method.invoke(instance)
 *
 * This is the reflection pattern discussed earlier — every call site
 * that needs something from the extracted classes goes through this
 * loader instead of a direct reference.
 */
