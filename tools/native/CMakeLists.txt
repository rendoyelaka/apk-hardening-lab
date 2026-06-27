cmake_minimum_required(VERSION 3.22.1)
project("decryptloader")

# This module uses OpenSSL's EVP interface for AES-256-GCM.
#
# We deliberately do NOT use find_package(OpenSSL) here. The prebuilt
# Android OpenSSL packages (e.g. 217heidai/openssl_for_android) ship
# only raw .a static libraries and headers, with no .pc/pkg-config or
# CMake config files — find_package's detection heuristics can fail
# to recognize them even when given OPENSSL_ROOT_DIR, especially when
# cross-compiling via the Android NDK toolchain file.
#
# Instead, pass the OpenSSL root directly via -DOPENSSL_ANDROID_ROOT=<path>
# pointing at the folder containing include/ and lib/libcrypto.a —
# this script wires it up directly with no detection magic involved.

if(NOT DEFINED OPENSSL_ANDROID_ROOT)
    message(FATAL_ERROR "OPENSSL_ANDROID_ROOT must be set to the prebuilt OpenSSL folder (containing include/ and lib/libcrypto.a)")
endif()

set(OPENSSL_INCLUDE_DIR "${OPENSSL_ANDROID_ROOT}/include")
set(OPENSSL_CRYPTO_LIB "${OPENSSL_ANDROID_ROOT}/lib/libcrypto.a")

if(NOT EXISTS "${OPENSSL_CRYPTO_LIB}")
    message(FATAL_ERROR "libcrypto.a not found at ${OPENSSL_CRYPTO_LIB}")
endif()

add_library(decryptloader SHARED
        decrypt_loader.cpp)

target_include_directories(decryptloader PRIVATE "${OPENSSL_INCLUDE_DIR}")

find_library(log-lib log)

target_link_libraries(decryptloader
        "${OPENSSL_CRYPTO_LIB}"
        ${log-lib})
