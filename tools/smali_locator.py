#!/usr/bin/env python3
"""
smali_locator.py
Stage 2 of the pipeline.

Goal: split the decompiled smali tree into two sets:
  1. KEEP (loader dex)   — classes Android must load at app boot:
                           anything declared in AndroidManifest.xml as
                           <application>, <activity>, <service>,
                           <receiver>, <provider>, plus their direct
                           superclass chain where resolvable from smali.
  2. EXTRACT (logic dex) — everything else. This is the set that gets
                           pulled into its own dex, encrypted, and
                           loaded at runtime via InMemoryDexClassLoader.

This script only DETERMINES the split and writes two lists to disk
(keep_classes.txt, extract_classes.txt). It does not move files yet —
dex_assembler.py consumes these lists in the next stage.
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DECODED_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/decoded")
MANIFEST_PATH = DECODED_DIR / "AndroidManifest.xml"
OUTPUT_DIR = Path("build")

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def find_smali_roots(decoded_dir: Path):
    """Return all smali/ smali_classes2/ etc. roots under the decoded tree."""
    return sorted(p for p in decoded_dir.glob("smali*") if p.is_dir())


def class_name_to_smali_path(class_name: str) -> str:
    """com.example.Foo -> com/example/Foo.smali"""
    return class_name.replace(".", "/") + ".smali"


def get_manifest_component_classes(manifest_path: Path) -> set:
    """
    Parse AndroidManifest.xml and return the fully-qualified class names
    of every <application>, <activity>, <service>, <receiver>, <provider>
    entry. Handles relative names starting with '.' using the package attr.
    """
    if not manifest_path.exists():
        print(f"[X] Manifest not found at {manifest_path}")
        sys.exit(1)

    tree = ET.parse(manifest_path)
    root = tree.getroot()
    package = root.attrib.get("package", "")

    component_tags = ["application", "activity", "activity-alias",
                       "service", "receiver", "provider"]

    classes = set()
    for tag in component_tags:
        for el in root.iter(tag):
            name = el.attrib.get(f"{ANDROID_NS}name")
            if not name:
                continue
            if name.startswith("."):
                full = package + name
            elif "." not in name:
                full = package + "." + name
            else:
                full = name
            classes.add(full)

    return classes


def find_smali_file(decoded_dir: Path, class_name: str):
    """Search all smali roots for this class's .smali file. Returns Path or None."""
    rel_path = class_name_to_smali_path(class_name)
    for root in find_smali_roots(decoded_dir):
        candidate = root / rel_path
        if candidate.exists():
            return candidate
    return None


def get_superclass_from_smali(smali_path: Path):
    """Read a .smali file's .super line to find its parent class, if any."""
    text = smali_path.read_text(errors="ignore")
    match = re.search(r"^\.super\s+L([^;]+);", text, re.MULTILINE)
    if match:
        return match.group(1).replace("/", ".")
    return None


def collect_keep_set(decoded_dir: Path, manifest_classes: set) -> set:
    """
    For each manifest-declared class, walk up its .super chain (within the
    app's own smali tree — stop once we hit android.* or java.* framework
    classes, which don't need extracting since they're not in the APK dex).
    """
    keep = set()
    to_process = list(manifest_classes)

    while to_process:
        cls = to_process.pop()
        if cls in keep:
            continue
        keep.add(cls)

        smali_file = find_smali_file(decoded_dir, cls)
        if smali_file is None:
            # Likely a framework class (android.app.Activity etc.) —
            # nothing to extract, nothing further to walk.
            continue

        parent = get_superclass_from_smali(smali_file)
        if parent and parent not in keep:
            to_process.append(parent)

    return keep


def collect_all_classes(decoded_dir: Path) -> set:
    """Every class in every smali root, as dotted class names."""
    all_classes = set()
    for root in find_smali_roots(decoded_dir):
        for smali_file in root.rglob("*.smali"):
            rel = smali_file.relative_to(root).with_suffix("")
            all_classes.add(str(rel).replace("/", "."))
    return all_classes


def main():
    print(f"[*] Reading manifest: {MANIFEST_PATH}")
    manifest_classes = get_manifest_component_classes(MANIFEST_PATH)
    print(f"[*] Found {len(manifest_classes)} manifest-declared component classes")

    print("[*] Walking superclass chains to build KEEP set...")
    keep_set = collect_keep_set(DECODED_DIR, manifest_classes)
    print(f"[*] KEEP set (loader dex): {len(keep_set)} classes")

    print("[*] Scanning all smali files for full class list...")
    all_classes = collect_all_classes(DECODED_DIR)
    print(f"[*] Total classes found in APK: {len(all_classes)}")

    extract_set = all_classes - keep_set
    print(f"[*] EXTRACT set (logic dex, to encrypt): {len(extract_set)} classes")

    OUTPUT_DIR.mkdir(exist_ok=True)
    keep_file = OUTPUT_DIR / "keep_classes.txt"
    extract_file = OUTPUT_DIR / "extract_classes.txt"

    keep_file.write_text("\n".join(sorted(keep_set)) + "\n")
    extract_file.write_text("\n".join(sorted(extract_set)) + "\n")

    print(f"[OK] Wrote {keep_file} and {extract_file}")
    print("[*] Review extract_classes.txt before proceeding to dex_assembler.py —")
    print("    confirm nothing unexpected (e.g. a class you need at boot) is in there.")


if __name__ == "__main__":
    main()
