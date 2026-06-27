#!/usr/bin/env python3
"""
final_assembly.py
Stage 6 — the last automatable step.
"""

import re
import shutil
import subprocess
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
PUBLIC_BROKEN_TYPE_ENTRY_RE = re.compile(r'<public[^>]*\btype="type\d+"[^>]*/>\s*\n?')
MAX_BUILD_ATTEMPTS = 15

# Matches both failure styles aapt2 prints for unrecoverable resource files:
#   W: error: invalid file path '/abs/path/res/type1/anim0004.xml'.
#   W: /abs/path/res/values/animators.xml:3: error: invalid value for type 'X'. ...
INVALID_FILE_PATH_RE = re.compile(r"invalid file path '([^']+)'")
FILE_FAILED_TO_COMPILE_RE = re.compile(r"(/\S+\.xml): error: file failed to compile")
INVALID_VALUE_FOR_TYPE_RE = re.compile(r"(/\S+\.xml):\d+: error: invalid value for type")
INVALID_RESOURCE_TYPE_IN_PUBLIC_RE = re.compile(r"(/\S+\.xml):\d+: error: invalid resource type")
NO_DEFINITION_FOR_SYMBOL_RE = re.compile(
    r"no definition for declared symbol '[^:]+:([^/]+)/([^']+)'"
)
RESOURCE_NOT_FOUND_RE = re.compile(
    r"resource\s+([^\s/]+)/(\S+?)\s*\(aka[^)]*\)\s*not found"
)
ITEM_NAME_ENTRY_RE_TEMPLATE = (
    r'<item[^>]*\bname="{name}"[^>]*>.*?</item>\s*\n?'
    r'|<item[^>]*\bname="{name}"[^>]*/>\s*\n?'
)


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


def strip_broken_public_entries():
    """res/values/public.xml declares <public type="typeN" .../> entries
    for resource types whose real names were stripped from the original
    APK's resource table (commonly by R8's resource shrinker). 'typeN' is
    apktool's placeholder, not a real Android resource type, so aapt2
    always rejects it. There is no real name to recover here, so the only
    option is to drop these specific declarations; every correctly-named
    type (string, drawable, layout, etc.) is left untouched."""
    public_xml = FINAL_PROJECT_DIR / "res" / "values" / "public.xml"
    if not public_xml.exists():
        return
    original = public_xml.read_text(encoding="utf-8")
    cleaned, count = PUBLIC_BROKEN_TYPE_ENTRY_RE.subn("", original)
    if count:
        public_xml.write_text(cleaned, encoding="utf-8")
        print(f"[*] Removed {count} broken <public type=\"typeN\"> entr"
              f"{'y' if count == 1 else 'ies'} from res/values/public.xml")


def strip_dangling_public_symbols(stderr_text: str) -> int:
    """aapt2's link stage fails with 'no definition for declared symbol
    pkg:type/name' when public.xml still declares a resource whose
    backing file/entry no longer exists (because it was removed earlier
    in this same self-healing loop, or was already missing/corrupted in
    the original APK). Strip exactly those <public type="X" name="Y" .../>
    lines; everything else in public.xml is left untouched."""
    public_xml = FINAL_PROJECT_DIR / "res" / "values" / "public.xml"
    if not public_xml.exists():
        return 0

    pairs = set(NO_DEFINITION_FOR_SYMBOL_RE.findall(stderr_text))
    if not pairs:
        return 0

    content = public_xml.read_text(encoding="utf-8")
    removed = 0
    for res_type, res_name in pairs:
        entry_re = re.compile(
            r'<public[^>]*\btype="' + re.escape(res_type) +
            r'"[^>]*\bname="' + re.escape(res_name) + r'"[^>]*/>\s*\n?'
        )
        # public.xml attribute order isn't guaranteed to be type-then-name,
        # so also try name-then-type.
        entry_re_alt = re.compile(
            r'<public[^>]*\bname="' + re.escape(res_name) +
            r'"[^>]*\btype="' + re.escape(res_type) + r'"[^>]*/>\s*\n?'
        )
        content, n1 = entry_re.subn("", content)
        content, n2 = entry_re_alt.subn("", content)
        removed += n1 + n2

    if removed:
        public_xml.write_text(content, encoding="utf-8")
    return removed


def strip_dangling_value_items(stderr_text: str) -> int:
    """aapt2's link stage also fails with 'resource type/name (aka
    pkg:type/name) not found' when a values file (styles.xml, themes.xml,
    etc. — across any values*/ variant directory) still has an <item>
    referencing an attr/resource whose definition no longer exists
    (already removed earlier in this loop, or already broken in the
    original APK). Strip exactly the matching <item name="..."> entries
    from every values*.xml file under res/; everything else is untouched."""
    res_dir = FINAL_PROJECT_DIR / "res"
    if not res_dir.exists():
        return 0

    names = {name for _, name in RESOURCE_NOT_FOUND_RE.findall(stderr_text)}
    if not names:
        return 0

    removed_total = 0
    for values_dir in res_dir.glob("values*"):
        if not values_dir.is_dir():
            continue
        for xml_file in values_dir.glob("*.xml"):
            content = xml_file.read_text(encoding="utf-8")
            new_content = content
            for name in names:
                pattern = re.compile(
                    ITEM_NAME_ENTRY_RE_TEMPLATE.format(name=re.escape(name)),
                    re.DOTALL,
                )
                new_content, n = pattern.subn("", new_content)
                removed_total += n
            if new_content != content:
                xml_file.write_text(new_content, encoding="utf-8")

    return removed_total


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
    strip_broken_public_entries()

    print(f"[!] NOTE: native .so libraries are NOT added by this script.")
    print(f"[!] NOTE: this APK is NOT signed by this script.")
    print(f"[!] Both must be added before this APK will actually install/run.")


def extract_broken_resource_files(stderr_text: str):
    """Parse aapt2's stderr for every resource file it could not compile,
    regardless of which of the two failure message styles it used."""
    broken = set()
    for line in stderr_text.splitlines():
        m = INVALID_FILE_PATH_RE.search(line)
        if m:
            broken.add(Path(m.group(1)))
            continue
        m = FILE_FAILED_TO_COMPILE_RE.search(line)
        if m:
            broken.add(Path(m.group(1)))
            continue
        m = INVALID_VALUE_FOR_TYPE_RE.search(line)
        if m:
            broken.add(Path(m.group(1)))
            continue
        m = INVALID_RESOURCE_TYPE_IN_PUBLIC_RE.search(line)
        if m:
            broken.add(Path(m.group(1)))
    return broken


def run_apktool_build():
    cmd = ["apktool", "b", str(FINAL_PROJECT_DIR), "-o", str(OUTPUT_APK), "--use-aapt2"]

    for attempt in range(1, MAX_BUILD_ATTEMPTS + 1):
        print(f"[*] Running (attempt {attempt}/{MAX_BUILD_ATTEMPTS}): {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            print(f"[OK] Built final (unsigned, no .so) APK at {OUTPUT_APK}")
            print(f"[*] Size: {OUTPUT_APK.stat().st_size / 1024:.1f} KB")
            return

        combined = result.stdout + "\n" + result.stderr

        dangling_removed = strip_dangling_public_symbols(combined)
        dangling_items_removed = strip_dangling_value_items(combined)
        if dangling_removed or dangling_items_removed:
            if dangling_removed:
                print(f"[*] Removed {dangling_removed} dangling <public> entr"
                      f"{'y' if dangling_removed == 1 else 'ies'} from "
                      f"res/values/public.xml (symbol no longer backed by any "
                      f"resource file)")
            if dangling_items_removed:
                print(f"[*] Removed {dangling_items_removed} dangling <item> "
                      f"reference{'s' if dangling_items_removed != 1 else ''} "
                      f"from values*.xml files (resource not found) — retrying:")
            continue

        broken_files = extract_broken_resource_files(combined)
        existing_broken = [p for p in broken_files if p.exists()]

        if not existing_broken:
            print("[X] Final apktool build failed (no further auto-fixable "
                  "resource files identified)")
            print("[DEBUG] Raw combined output for diagnosis:")
            print(combined[-6000:])
            sys.exit(1)

        print(f"[*] aapt2 rejected {len(existing_broken)} resource file(s) this "
              f"attempt (unresolved/corrupted type names inherited from the "
              f"original APK's resource table). Removing and retrying:")
        for p in sorted(existing_broken):
            if p.name == "public.xml":
                print(f"      - {p} (re-stripping broken <public> entries instead of deleting)")
                original = p.read_text(encoding="utf-8")
                cleaned, count = PUBLIC_BROKEN_TYPE_ENTRY_RE.subn("", original)
                if count:
                    p.write_text(cleaned, encoding="utf-8")
                else:
                    # nothing left to strip safely; avoid an infinite loop
                    p.unlink()
            else:
                print(f"      - {p}")
                p.unlink()

    print(f"[X] Final apktool build still failing after {MAX_BUILD_ATTEMPTS} "
          f"auto-fix attempts. Last error:")
    print(result.stdout[-4000:])
    print(result.stderr[-4000:])
    sys.exit(1)


def main():
    scaffold_final_project()
    run_apktool_build()


if __name__ == "__main__":
    main()
