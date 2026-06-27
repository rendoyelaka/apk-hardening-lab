#!/usr/bin/env python3
"""
extract_and_encrypt.py
Orchestrator for the APK protection pipeline.
Stage 1: decompile only — confirms apktool works on this APK before anything else.
"""

import subprocess
import sys
from pathlib import Path

APK_PATH = sys.argv[1] if len(sys.argv) > 1 else "app.apk"
BUILD_DIR = Path("build")
DECODED_DIR = BUILD_DIR / "decoded"


def decompile_apk(apk_path: str, output_dir: Path) -> bool:
    """Run apktool to decompile the APK into smali + resources."""
    if output_dir.exists():
        print(f"[!] {output_dir} already exists — removing for a clean decompile")
        import shutil
        shutil.rmtree(output_dir)

    cmd = ["apktool", "d", apk_path, "-o", str(output_dir), "--no-res"]
    print(f"[*] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print("[X] Decompile failed")
        print(result.stderr)
        return False

    print(f"[OK] Decompiled successfully into {output_dir}")
    return True


def main():
    BUILD_DIR.mkdir(exist_ok=True)

    if not Path(APK_PATH).exists():
        print(f"[X] APK not found: {APK_PATH}")
        sys.exit(1)

    if not decompile_apk(APK_PATH, DECODED_DIR):
        sys.exit(1)

    # Next stages (smali_locator, dex_assembler, encrypt_dex) get wired in here
    # once decompile is confirmed working on your actual APK.
    print("[*] Stage 1 complete. Inspect build/decoded/smali*/ to confirm com/myapp/in is present.")


if __name__ == "__main__":
    main()
