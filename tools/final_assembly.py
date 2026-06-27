#!/usr/bin/env python3
"""
final_assembly.py
Stage 6 — the last automatable step.
"""

import re
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

TYPE_FOLDER_RE = re.compile(r"^type\d+$")


def find_smali_roots(decoded_dir: Path):
    return sorted(p for p in decoded_dir.glob("smali*") if p.is_dir())


def class_name_to_smali_rel(class_name: str) -> str:
    return class_name.replace(".", "/") + ".smali"


def strip_unresolved_type_folders():
    res_dir = FINAL_PROJECT_DIR / "res"
    if not res_dir.exists():
        return
    removed = []
    for entry in res_dir.iterdir():
        if entry.is_dir() and TYPE_FOLDER_RE.match(entry.name):
            removed.append(entry.name)
            shutil.rmtree(entry)
    if removed:
        print(f"[*] Removed unresolved resource type folders: {', '.join(sorted(removed))}")


def scaffold_final_project():
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

    strip_unresolved_type_folders()

    print(f"[!] NOTE: native .so libraries are NOT added by this script.")
    print(f"[!] NOTE: this APK is NOT signed by this script.")
    print(f"[!] Both must be added before this APK will actually install/run.")


def run_apktool_build():
    import subprocess
    cmd = ["apktool", "b", str(FINAL_PROJECT_DIR), "-o", str(OUTPUT_APK), "--use-aapt2"]
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
