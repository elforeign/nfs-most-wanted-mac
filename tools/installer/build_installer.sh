#!/bin/sh
# Builds the Setup app and its disk image from this repository: dist/NFS-Most-Wanted-Native-Setup-<version>.dmg.
# No game files are involved: the app carries this repository's source (git archive of HEAD) and runs setup.sh.
# Usage: tools/installer/build_installer.sh [version]      (default: the current git tag or commit)
set -eu
cd "$(dirname "$0")/../.."
VERSION="${1:-$(git describe --tags --always 2>/dev/null || echo dev)}"
SHORT="$(echo "$VERSION" | sed 's/^v//; s/[^0-9.].*$//')"; [ -n "$SHORT" ] || SHORT=0.0.0
if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    echo "Note: uncommitted changes are not included (the app carries HEAD)."
fi
OUT=build/installer
NAME="NFS Most Wanted Native Setup"
rm -rf "$OUT"; mkdir -p "$OUT/dmg"
APP="$OUT/dmg/$NAME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -parse-as-library -target arm64-apple-macos13 -o "$APP/Contents/MacOS/Setup" tools/installer/SetupApp.swift
git archive --format=tar.gz -o "$APP/Contents/Resources/source.tar.gz" HEAD
printf '%s\n' "$VERSION" > "$APP/Contents/Resources/source-version.txt"
cp tools/installer/ReadMe.txt "$APP/Contents/Resources/ReadMe.txt"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>Setup</string>
  <key>CFBundleIdentifier</key><string>io.github.nfsmw-native-mac.setup</string>
  <key>CFBundleName</key><string>NFSMW Setup</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$SHORT</string>
  <key>CFBundleVersion</key><string>$SHORT</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF
codesign --force --sign - "$APP"
cp tools/installer/ReadMe.txt "$OUT/dmg/Read Me First.txt"
chmod 644 "$OUT/dmg/Read Me First.txt"; xattr -c "$OUT/dmg/Read Me First.txt" 2>/dev/null || true
mkdir -p dist
DMG="dist/NFS-Most-Wanted-Native-Setup-$VERSION.dmg"
rm -f "$DMG"
hdiutil create -quiet -volname "$NAME" -srcfolder "$OUT/dmg" -ov -format UDZO "$DMG"
echo "$DMG ($(du -h "$DMG" | cut -f1)); app $(du -sh "$APP" | cut -f1)"
shasum -a 256 "$DMG"
