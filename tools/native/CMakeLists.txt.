cmake_minimum_required(VERSION 3.22.1)
project("decryptloader")

# This module uses OpenSSL's EVP interface for AES-256-GCM.
# On most Android NDK setups, OpenSSL isn't bundled by default —
# you'll need to either:
#   (a) use a prebuilt OpenSSL-for-Android package (e.g. via vcpkg,
#       or a precompiled .so/.a checked into this repo per ABI), or
#   (b) switch to BoringSSL / a small embedded AES-GCM implementation
#       if you don't want an external dependency.
# This CMakeLists assumes option (a): an OpenSSL package providing
# the imported targets OpenSSL::Crypto / OpenSSL::SSL is available.
# Stage 5 (CI build check) will surface whether this resolves cleanly
# in the GitHub Actions environment before we worry about on-device use.

find_package(OpenSSL REQUIRED)

add_library(decryptloader SHARED
        decrypt_loader.cpp)

find_library(log-lib log)

target_link_libraries(decryptloader
        OpenSSL::Crypto
        ${log-lib})
