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
MAX_BUILD_ATTEMPTS = 60

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
RESOURCE_NOT_FOUND_FILE_RE = re.compile(
    r"(/\S+\.xml):\d+: error: resource \S+/\S+ \(aka[^)]*\) not found"
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
    """aapt2's link stage fails with 'resource type/name (aka pkg:type/name)
    not found' when a values file (styles.xml, themes.xml, etc.) still
    references an attr/resource whose definition no longer exists. The
    <item> syntax that holds these references varies too much to safely
    pattern-match and strip individual lines, so the whole offending file
    is removed instead — consistent with how every other unrecoverable
    resource file in this pipeline is handled."""
    files = {Path(p) for p in RESOURCE_NOT_FOUND_FILE_RE.findall(stderr_text)}
    removed = 0
    for p in files:
        if p.exists():
            p.unlink()
            removed += 1
    return removed


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
    proactively_removed = strip_orphaned_public_entries()
    if proactively_removed:
        print(f"[*] Proactively removed {proactively_removed} orphaned "
              f"<public> entries from res/values/public.xml (no backing "
              f"resource found anywhere in res/)")

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


PUBLIC_ENTRY_RE = re.compile(
    r'<public[^>]*\btype="([^"]+)"[^>]*\bname="([^"]+)"[^>]*/>'
    r'|<public[^>]*\bname="([^"]+)"[^>]*\btype="([^"]+)"[^>]*/>'
)

# Resource types whose definitions live in res/values*/*.xml as <TYPE
# name="..."> tags rather than as files (style, attr, id, declare-styleable,
# bool, integer, color, dimen, string, plurals, array, string-array,
# integer-array, animator/interpolator entries also live here when they
# aren't separate files).
VALUE_BASED_TYPES = {
    "style", "attr", "id", "bool", "integer", "color", "dimen", "string",
    "plurals", "array", "fraction", "declare-styleable",
}

# Resource types that are backed by a file (one file = one resource),
# located under a res/<type>(-<qualifier>)? directory.
FILE_BASED_TYPE_DIRS = {
    "drawable", "layout", "anim", "animator", "interpolator", "menu",
    "mipmap", "raw", "xml", "color", "transition", "font", "navigation",
}


def collect_defined_value_names(res_dir: Path) -> dict:
    """Scan every values*/*.xml file and collect the set of names defined
    for each value-based resource type (e.g. {'style': {'Foo', 'Bar'}})."""
    defined = {t: set() for t in VALUE_BASED_TYPES}
    tag_re = re.compile(r'<(style|attr|bool|integer|color|dimen|string|'
                         r'plurals|array|fraction|declare-styleable|item)'
                         r'[^>]*\bname="([^"]+)"')
    for values_dir in res_dir.glob("values*"):
        if not values_dir.is_dir():
            continue
        for xml_file in values_dir.glob("*.xml"):
            if xml_file.name == "public.xml":
                continue
            try:
                content = xml_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for tag, name in tag_re.findall(content):
                # <item> tags are ambiguous (used for many types); we only
                # use the unambiguous tag names directly above for value
                # types. <item> itself is skipped here deliberately.
                if tag in defined:
                    defined[tag].add(name)
    return defined


def collect_defined_file_names(res_dir: Path) -> dict:
    """For file-based resource types, collect the set of base filenames
    (without extension) that actually exist under res/<type>*/ dirs."""
    defined = {t: set() for t in FILE_BASED_TYPE_DIRS}
    for type_name in FILE_BASED_TYPE_DIRS:
        for type_dir in res_dir.glob(f"{type_name}*"):
            if not type_dir.is_dir():
                continue
            for f in type_dir.iterdir():
                if f.is_file():
                    defined[type_name].add(f.stem)
    return defined


def strip_orphaned_public_entries() -> int:
    """Proactively cross-check every <public type="X" name="Y" .../> entry
    in res/values/public.xml against what's actually defined in res/ (as a
    file or as a values-XML tag). Strip every entry with no backing
    definition in a single pass, instead of waiting for aapt2 to reveal a
    handful of them per build attempt."""
    public_xml = FINAL_PROJECT_DIR / "res" / "values" / "public.xml"
    res_dir = FINAL_PROJECT_DIR / "res"
    if not public_xml.exists() or not res_dir.exists():
        return 0

    defined_values = collect_defined_value_names(res_dir)
    defined_files = collect_defined_file_names(res_dir)

    content = public_xml.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    kept_lines = []
    removed = 0

    entry_re = re.compile(
        r'<public[^>]*\btype="([^"]+)"[^>]*\bname="([^"]+)"[^>]*/>'
        r'|<public[^>]*\bname="([^"]+)"[^>]*\btype="([^"]+)"[^>]*/>'
    )

    for line in lines:
        m = entry_re.search(line)
        if not m:
            kept_lines.append(line)
            continue
        if m.group(1) is not None:
            res_type, res_name = m.group(1), m.group(2)
        else:
            res_name, res_type = m.group(3), m.group(4)

        if res_type in defined_values:
            backed = res_name in defined_values[res_type]
        elif res_type in defined_files:
            backed = res_name in defined_files[res_type]
        else:
            # Unknown/unhandled type category — leave it alone rather
            # than risk deleting something we can't verify either way.
            kept_lines.append(line)
            continue

        if backed:
            kept_lines.append(line)
        else:
            removed += 1

    if removed:
        public_xml.write_text("".join(kept_lines), encoding="utf-8")
    return removed


def run_apktool_build():
    cmd = ["apktool", "b", str(FINAL_PROJECT_DIR), "-o", str(OUTPUT_APK), "--use-aapt2", "-f"]

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
