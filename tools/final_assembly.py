#!/usr/bin/env python3
"""
final_assembly.py
Stage 6 — the last automatable step.

Builds the actual KEEP project that becomes your final APK:
  1. Start from the real decoded APK structure (manifest, resources, etc.)
  2. Strip out all EXTRACT-set smali (the 1889 classes already pulled
     into the encrypted blob) so they're not duplicated
  3. Copy in the loader smali (NativeLoader + its companion) produced
     by baksmali in Stage 5
  4. Copy assets/logic.bin into the project's assets folder
  5. Run apktool b on this assembled project to produce final.apk

This does NOT sign the APK or add the compiled .so files — those are
separate remaining steps (signing needs your keystore; .so placement
needs per-ABI native builds, only arm64-v8a has been build-checked
so far). This stage produces an APK that is structurally complete for
everything CI can verify, but not yet installable until those two
pieces are added.
"""

import shutil
import sys
from pathlib import Path

DECODED_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/decoded")
KEEP_LIST = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("build/keep_classes.txt")
EXTRACT_LIST = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("build/extract_classes.txt")
LOADER_SMALI_DIR = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("build/loader-module-smali")
ENCRYPTED_BLOB = Path(sys.argv[5]) if len(sys.argv) > 5 else Path("assets/logic.bin")

FINAL_PROJECT_DIR = Path("build/final_project")
OUTPUT_APK = Path("build/final_unsigned.apk")


def find_smali_roots(decoded_dir: Path):
    return sorted(p for p in decoded_dir.glob("smali*") if p.is_dir())


def class_name_to_smali_rel(class_name: str) -> str:
    return class_name.replace(".", "/") + ".smali"


def scaffold_final_project():
    """Copy the full decoded structure, then strip EXTRACT classes
    and add the loader smali + encrypted asset."""
    if FINAL_PROJECT_DIR.exists():
        print(f"[!] {FINAL_PROJECT_DIR} exists — removing for a clean build")
        shutil.rmtree(FINAL_PROJECT_DIR)

    print(f"[*] Copying full decoded project from {DECODED_DIR}...")
    shutil.copytree(DECODED_DIR, FINAL_PROJECT_DIR)

    extract_classes = [
        line.strip() for line in EXTRACT_LIST.read_text().splitlines() if line.strip()
    ]
    print(f"[*] Removing {len(extract_classes)} EXTRACT-set smali files from final project...")

    removed, missing = 0, 0
    for class_name in extract_classes:
        rel = class_name_to_smali_rel(class_name)
        found_any = False
        for root in find_smali_roots(FINAL_PROJECT_DIR):
            candidate = root / rel
            if candidate.exists():
                candidate.unlink()
                removed += 1
                found_any = True
        if not found_any:
            missing += 1

    print(f"[*] Removed {removed} smali files ({missing} were already absent)")

    print(f"[*] Copying loader smali (NativeLoader) into final project...")
    final_smali_root = FINAL_PROJECT_DIR / "smali"
    loader_files_copied = 0
    for smali_file in LOADER_SMALI_DIR.rglob("*.smali"):
        rel = smali_file.relative_to(LOADER_SMALI_DIR)
        dest = final_smali_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(smali_file, dest)
        loader_files_copied += 1
    print(f"[*] Copied {loader_files_copied} loader smali files")

    print(f"[*] Copying encrypted blob into final project assets...")
    final_assets_dir = FINAL_PROJECT_DIR / "assets"
    final_assets_dir.mkdir(exist_ok=True)
    shutil.copy(ENCRYPTED_BLOB, final_assets_dir / "logic.bin")

    print(f"[!] NOTE: native .so libraries are NOT added by this script.")
    print(f"[!] NOTE: this APK is NOT signed by this script.")
    print(f"[!] Both must be added before this APK will actually install/run.")


def run_apktool_build():
    import subprocess
    cmd = ["apktool", "b", str(FINAL_PROJECT_DIR), "-o", str(OUTPUT_APK)]
    print(f"[*] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print("[X] Final apktool build failed")
        print(result.stdout[-4000:])
        print(result.stderr[-4000:])
        sys.exit(1)

    print(f"[OK] Built final (unsigned, no .so) APK at {OUTPUT_APK}")
    print(f"[*] Size: {OUTPUT_APK.stat().st_size / 1024:.1f} KB")


def main():
    scaffold_final_project()
    run_apktool_build()


if __name__ == "__main__":
    main()
