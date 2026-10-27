#!/bin/sh
# Builds the Setup app and its disk image from this checkout:
#   dist/NFS-Most-Wanted-Native-Setup-v<version>.dmg  and  dist/NFS-Most-Wanted-Native-Setup-v<version>.dmg.sha256
# No game files are involved. The app carries the reviewed source tree (git archive of HEAD; in the maintainer's
# checkout passed through the publication importer, so only allowlisted files, redacted and scanned), the pinned relocatable Python with its
# wheels, and the pinned FFmpeg source tarball (tools/installer/deps.lock.json).
#
# Usage: tools/installer/build_installer.sh [version]            full release build (version like 4.0.0 or v4.0.0)
#        tools/installer/build_installer.sh --dev [version]      skip the pinned dependencies (not releasable; output in build/)
#        tools/installer/build_installer.sh --print-names VERSION    print the DMG and checksum file names, build nothing
#        tools/installer/build_installer.sh --payload-only OUTDIR [VERSION]   write OUTDIR/source.tar.gz only
# Environment: PYTHON (default python3) runs the importer and scanner when present; no network is used except fetching missing
# dependencies listed in deps.lock.json, each checked against its SHA-256.
set -eu

MIN_MACOS=14.5          # the one place the macOS floor is set (raise or lower it here)
PRODUCT="NFS-Most-Wanted-Native-Setup"
NAME="NFS Most Wanted Native Setup"
PYTHON="${PYTHON:-python3}"

die() { echo "build_installer: $*" >&2; exit 1; }

# Accepts 4.0.0 or v4.0.0 (2 to 4 numeric parts); prints the number without the v.
norm_version() {
    v="${1#v}"
    printf '%s\n' "$v" | grep -Eq '^[0-9]+(\.[0-9]+){1,3}$' || die "version must look like 4.0.0 (got '$1')"
    printf '%s\n' "$v"
}
dmg_name() { nv="$(norm_version "$1")" || exit 1; echo "$PRODUCT-v$nv.dmg"; }

MODE=release
case "${1:-}" in
    --print-names) [ $# -eq 2 ] || die "usage: --print-names VERSION"
        n="$(dmg_name "$2")"; echo "$n"; echo "$n.sha256"; exit 0 ;;
    --payload-only) MODE=payload; shift ;;
    --dev) MODE=dev; shift ;;
esac

cd "$(dirname "$0")/../.."
ROOT="$(pwd -P)"
OUT="$ROOT/build/installer"

OUTDIR=""
if [ "$MODE" = payload ]; then OUTDIR="${1:?--payload-only needs an output directory}"; shift; fi
VERSION_RAW="${1:-$(git describe --tags --abbrev=0 2>/dev/null || true)}"
[ -n "$VERSION_RAW" ] || die "no version given and no git tag found"
VERSION="$(norm_version "$VERSION_RAW")"

if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    echo "Note: uncommitted changes are not included (the app carries HEAD)." >&2
fi

# --- 1. The source payload: git archive of HEAD -> tar.gz -----------------------------------------------------------
# A checkout that has the publication importer (tools/publish, the maintainer's private checkout) passes the archive
# through it (allowlist + redaction + scan). A public checkout has no importer: its tracked tree is already the
# published selection, so the archive is packaged as it is.
make_payload() {
    dest="$1"
    rm -rf "$OUT/src" "$OUT/public"; mkdir -p "$OUT/src" "$OUT/public"
    git rev-parse --verify 'HEAD^{tree}' >/dev/null 2>&1 || die "no usable HEAD source tree"
    git archive --format=tar HEAD > "$OUT/source.tar" || die "could not archive HEAD"
    [ -s "$OUT/source.tar" ] || die "HEAD archive is empty"
    tar -tf "$OUT/source.tar" > "$OUT/source.list" || die "HEAD archive is invalid"
    [ -s "$OUT/source.list" ] || die "HEAD archive contains no files"
    importer_entry="$(git ls-tree HEAD -- tools/publish/import.py)" || die "could not inspect HEAD"
    if [ -n "$importer_entry" ]; then
        tar -xf "$OUT/source.tar" -C "$OUT/src" || die "could not extract HEAD"
        # The importer selects Git-tracked files only: track exactly HEAD's files in the extracted copy.
        git -C "$OUT/src" init -q && git -C "$OUT/src" add -A -f \
            || die "could not index the extracted source tree"
        "$PYTHON" -I "$OUT/src/tools/publish/import.py" "$OUT/src" "$OUT/public" --summary-only >&2 \
            || die "the publication importer refused the tree (allowlist or scan findings); nothing was packaged"
    else
        tar -xf "$OUT/source.tar" -C "$OUT/public" || die "could not extract HEAD"
    fi
    # The importer's ownership marker is not part of the payload.
    rm -f "$OUT/public/tools/publish/owned.txt"; rmdir "$OUT/public/tools/publish" 2>/dev/null || true
    mkdir -p "$dest"
    COPYFILE_DISABLE=1 tar -czf "$dest/source.tar.gz" -C "$OUT/public" .
}

if [ "$MODE" = payload ]; then
    make_payload "$OUTDIR"
    echo "$OUTDIR/source.tar.gz"
    exit 0
fi

# --- 2. Pinned dependencies (skipped in --dev) --------------------------------------------------------------------
LOCK=tools/installer/deps.lock.json
DL="$OUT/dl"
fetch_deps() {
    mkdir -p "$DL"
    "$PYTHON" -I - "$LOCK" "$DL" <<'PY'
import hashlib, json, os, subprocess, sys
lock, dl = sys.argv[1:3]
data = json.load(open(lock))
items = [("python", data["python"])] + [("wheel " + w["name"], w) for w in data["wheels"]] + \
        [("source " + s["name"], s) for s in data["sources"]]
todo = [n for n, i in items if not i.get("url") or not i.get("sha256")]
if todo:
    sys.exit("deps.lock.json still has unfilled entries (TODO): " + ", ".join(todo))
for name, i in items:
    fn = os.path.join(dl, os.path.basename(i["url"]))
    if not os.path.exists(fn):
        subprocess.check_call(["curl", "-fL", "--proto", "=https", "-o", fn + ".part", i["url"]])
        os.rename(fn + ".part", fn)
    h = hashlib.sha256(open(fn, "rb").read()).hexdigest()
    if h != i["sha256"]:
        os.remove(fn)
        sys.exit("checksum mismatch for %s (%s); removed it" % (name, fn))
print("dependencies verified:", len(items))
PY
}

# --- 3. The app ----------------------------------------------------------------------------------------------------
rm -rf "$OUT/dmg" "$OUT/app-build"; mkdir -p "$OUT/dmg"
APP="$OUT/dmg/$NAME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -parse-as-library -target "arm64-apple-macos$MIN_MACOS" -o "$APP/Contents/MacOS/Setup" tools/installer/SetupApp.swift
make_payload "$APP/Contents/Resources"
printf '%s\n' "$VERSION" > "$APP/Contents/Resources/source-version.txt"
sed "s/@MIN_MACOS@/$MIN_MACOS/g" tools/installer/ReadMe.txt > "$APP/Contents/Resources/ReadMe.txt"
cp tools/installer/deps.lock.json "$APP/Contents/Resources/deps.lock.json"

if [ "$MODE" = release ]; then
    fetch_deps
    "$PYTHON" -I - "$LOCK" "$DL" "$APP/Contents/Resources" <<'PY'
import json, os, subprocess, sys, shutil
lock, dl, res = sys.argv[1:4]
data = json.load(open(lock))
base = lambda i: os.path.join(dl, os.path.basename(i["url"]))
subprocess.check_call(["tar", "-xf", base(data["python"]), "-C", res])
py = os.path.join(res, "python", "bin", "python3")
if not os.path.exists(py):
    sys.exit("the Python archive did not unpack to python/bin/python3; check the pinned archive's layout")
wheels = [base(w) for w in data["wheels"]]
subprocess.check_call([py, "-I", "-m", "pip", "install", "--no-index", "--no-deps", "--only-binary", ":all:",
                       "--disable-pip-version-check"] + wheels)
subprocess.check_call([py, "-I", "-c", "import capstone, numpy, PIL"])
for s in data["sources"]:
    dest = os.path.join(res, s["dest"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy(base(s), dest)
PY
else
    echo "Note: --dev build without the pinned Python and FFmpeg; do not release it." >&2
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>Setup</string>
  <key>CFBundleIdentifier</key><string>org.nfsmw-native.setup</string>
  <key>CFBundleName</key><string>NFSMW Setup</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

# Ad hoc signing, inside out: bundled Mach-O files first, then the app.
find "$APP/Contents/Resources" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -111 \) 2>/dev/null | while read -r f; do
    file "$f" | grep -q 'Mach-O' && codesign --force --sign - "$f" >/dev/null 2>&1 || true
done
codesign --force --sign - "$APP"
codesign --verify --strict "$APP"

# --- 4. The disk image and its checksum ----------------------------------------------------------------------------
sed "s/@MIN_MACOS@/$MIN_MACOS/g" tools/installer/ReadMe.txt > "$OUT/dmg/Read Me First.txt"
chmod 644 "$OUT/dmg/Read Me First.txt"; xattr -c "$OUT/dmg/Read Me First.txt" 2>/dev/null || true
DISTDIR="$ROOT/dist"; [ "$MODE" = dev ] && DISTDIR="$OUT/dev"
mkdir -p "$DISTDIR"
DMGNAME="$(dmg_name "$VERSION")"
DMG="$DISTDIR/$DMGNAME"
rm -f "$DMG"
hdiutil create -quiet -volname "$NAME" -srcfolder "$OUT/dmg" -ov -format UDZO "$DMG"
echo "$DMG ($(du -h "$DMG" | cut -f1))"
(cd "$DISTDIR" && shasum -a 256 "$DMGNAME" | tee "$DMGNAME.sha256")
