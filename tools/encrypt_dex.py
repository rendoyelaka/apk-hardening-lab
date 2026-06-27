#!/usr/bin/env python3
"""
encrypt_dex.py
Stage 4 of the pipeline.

Encrypts build/extracted_classes.dex with AES-256-GCM and writes the
result to assets/logic.bin (ready to be packaged into the APK's assets
folder). The encryption key is generated fresh per run and saved to
build/dex_encryption_key.txt (hex-encoded) — NOT placed in assets,
since that would be sitting right next to the thing it's meant to
protect.

This key file is a CI build artifact only. The native loader (written
in a later stage, in C/C++ via JNI) is what actually needs this key at
runtime — how it gets there (hardcoded after obfuscation, derived from
something else, fetched from a keystore, etc.) is a decision for that
stage. For now this script just makes the key visible so you can
choose deliberately rather than have it silently baked in somewhere.

Output layout, prepended to the ciphertext so the loader can read it
back out without needing a separate file:
    [12 bytes IV][ciphertext][16 bytes GCM tag]
(PyCryptodome's GCM mode appends the tag automatically when using
encrypt_and_digest, so we store iv + ciphertext + tag in that order.)
"""

import secrets
import sys
from pathlib import Path

try:
    from Crypto.Cipher import AES
except ImportError:
    print("[X] pycryptodome not installed. Run: pip install pycryptodome --break-system-packages")
    sys.exit(1)

INPUT_DEX = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/extracted_classes.dex")
ASSETS_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("assets")
OUTPUT_BLOB = ASSETS_DIR / "logic.bin"
KEY_FILE = Path("build/dex_encryption_key.txt")

KEY_SIZE_BYTES = 32  # AES-256
IV_SIZE_BYTES = 12   # recommended size for GCM


def encrypt_dex(plaintext: bytes, key: bytes) -> bytes:
    iv = secrets.token_bytes(IV_SIZE_BYTES)
    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return iv + ciphertext + tag


def main():
    if not INPUT_DEX.exists():
        print(f"[X] {INPUT_DEX} not found — run dex_assembler.py first")
        sys.exit(1)

    plaintext = INPUT_DEX.read_bytes()
    print(f"[*] Read {len(plaintext)} bytes from {INPUT_DEX}")

    key = secrets.token_bytes(KEY_SIZE_BYTES)
    print(f"[*] Generated random AES-256 key for this build")

    blob = encrypt_dex(plaintext, key)
    print(f"[*] Encrypted blob size: {len(blob)} bytes (IV + ciphertext + tag)")

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_BLOB.write_bytes(blob)
    print(f"[OK] Wrote encrypted blob to {OUTPUT_BLOB}")

    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key.hex() + "\n")
    print(f"[OK] Wrote key (hex) to {KEY_FILE}")
    print("[!] This key file is a build artifact, not for the APK assets.")
    print("[!] The native loader stage will need this key — keep it out of source control")
    print("[!] in any real (non-CI-testing) use; for this local/test pipeline it's fine")
    print("[!] to inspect, but treat it as sensitive once you're testing on a real key.")


if __name__ == "__main__":
    main()
