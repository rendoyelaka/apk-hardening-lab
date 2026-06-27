#!/usr/bin/env python3
"""
dex_assembler.py
Stage 3 of the pipeline.

Takes extract_classes.txt (from smali_locator.py) and builds those
classes into a standalone classes.dex, using apktool's own "b" (build)
command. apktool needs a minimal project structure to do this, so this
script scaffolds one:

  build/extract_project/
    AndroidManifest.xml   (copied from the real decoded APK)
    apktool.yml           (copied/adapted from the real decoded APK)
    smali/                (ONLY the classes listed in extract_classes.txt)

Then runs `apktool b build/extract_project -o build/extract.apk`,
which produces build/extract_project/build/apk/classes.dex — the dex
containing just the extracted classes. We pull that dex out and discard
the rest of the fake APK (resources, manifest etc. are not needed
downstream, only the dex bytes).
"""

import shutil
import subprocess
import sys
from pathlib import Path

DECODED_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/decoded")
EXTRACT_LIST = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("build/extract_classes.txt")
PROJECT_DIR = Path("build/extract_project")
OUTPUT_DEX = Path("build/extracted_classes.dex")


def class_name_to_smali_rel(class_name: str) -> str:
    return class_name.replace(".", "/") + ".smali"


def find_smali_roots(decoded_dir: Path):
    return sorted(p for p in decoded_dir.glob("smali*") if p.is_dir())


def scaffold_project(decoded_dir: Path, extract_classes: list, project_dir: Path):
    """Build a minimal apktool-buildable project containing only the
    extracted classes, plus the manifest/yml apktool needs to function."""
    if project_dir.exists():
        print(f"[!] {project_dir} exists — removing for a clean build")
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True)

    # apktool needs these to recognize the folder as a valid project
    manifest_src = decoded_dir / "AndroidManifest.xml"
    yml_src = decoded_dir / "apktool.yml"
    if not manifest_src.exists() or not yml_src.exists():
        print("[X] Missing AndroidManifest.xml or apktool.yml in decoded dir")
        sys.exit(1)

    shutil.copy(manifest_src, project_dir / "AndroidManifest.xml")
    shutil.copy(yml_src, project_dir / "apktool.yml")

    smali_dest_root = project_dir / "smali"
    smali_dest_root.mkdir()

    smali_roots = find_smali_roots(decoded_dir)
    copied, missing = 0, 0

    for class_name in extract_classes:
        rel = class_name_to_smali_rel(class_name)
        found = None
        for root in smali_roots:
            candidate = root / rel
            if candidate.exists():
                found = candidate
                break
        if found is None:
            missing += 1
            continue

        dest = smali_dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(found, dest)
        copied += 1

    print(f"[*] Copied {copied} smali files into extract project")
    if missing:
        print(f"[!] {missing} classes from the list had no matching .smali file (skipped)")


def run_apktool_build(project_dir: Path) -> Path:
    """Run apktool b and return the path to the resulting classes.dex."""
    cmd = ["apktool", "b", str(project_dir)]
    print(f"[*] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print("[X] apktool build failed")
        print(result.stdout[-3000:])
        print(result.stderr[-3000:])
        sys.exit(1)

    dex_path = project_dir / "build" / "apk" / "classes.dex"
    if not dex_path.exists():
        print(f"[X] Expected dex not found at {dex_path}")
        sys.exit(1)

    print(f"[OK] Built dex at {dex_path}")
    return dex_path


def main():
    if not EXTRACT_LIST.exists():
        print(f"[X] {EXTRACT_LIST} not found — run smali_locator.py first")
        sys.exit(1)

    extract_classes = [
        line.strip() for line in EXTRACT_LIST.read_text().splitlines() if line.strip()
    ]
    print(f"[*] Loaded {len(extract_classes)} classes to extract")

    scaffold_project(DECODED_DIR, extract_classes, PROJECT_DIR)
    dex_path = run_apktool_build(PROJECT_DIR)

    OUTPUT_DEX.parent.mkdir(exist_ok=True)
    shutil.copy(dex_path, OUTPUT_DEX)
    print(f"[OK] Copied final dex to {OUTPUT_DEX}")
    print(f"[*] Size: {OUTPUT_DEX.stat().st_size / 1024:.1f} KB")
    print("[*] Next stage: encrypt_dex.py will AES-encrypt this file into assets/logic.bin")


if __name__ == "__main__":
    main()
