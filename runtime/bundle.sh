#!/bin/sh
# Builds NFSMW.app from runtime/boot (run build.sh first):  runtime/bundle.sh [output dir, default runtime/]
# Public identity: --name NAME --exec-name EXE --bundle-id ID --version VERSION [--build-version VERSION] [--source-id HASH].
# With --name (or NFSMW_BUNDLE_NAME) the bundle directory is "NAME.app"; the executable is EXE (default NFSMW).
# Environment equivalents: NFSMW_BUNDLE_NAME, NFSMW_BUNDLE_EXEC, NFSMW_BUNDLE_ID, NFSMW_BUNDLE_VERSION,
# NFSMW_BUNDLE_BUILD_VERSION, NFSMW_SOURCE_ID. Omitting them keeps the owner defaults.
# The app asks for the game folder on first launch (Option at launch asks again), logs to ~/Library/Logs/NFSMW-port,
# and keeps saves and settings in ~/Library/Application Support/NFSMW-port (README "The app").
set -e
cd "$(dirname "$0")"
OUT=.
[ $# -eq 0 ] || { case "$1" in --*) ;; *) OUT=$1; shift ;; esac; }
NAME=${NFSMW_BUNDLE_NAME:-NFSMW}
EXEC_NAME=${NFSMW_BUNDLE_EXEC:-NFSMW}
BUNDLE_ID=${NFSMW_BUNDLE_ID:-local.nfsmw-port}
VERSION=${NFSMW_BUNDLE_VERSION:-}
BUILD_VERSION=${NFSMW_BUNDLE_BUILD_VERSION:-}
SOURCE_ID=${NFSMW_SOURCE_ID:-}
while [ $# -gt 0 ]; do
    [ $# -ge 2 ] || { echo "bundle.sh: missing option value" >&2; exit 2; }
    case "$1" in
        --name) NAME=$2 ;;
        --exec-name) EXEC_NAME=$2 ;;
        --bundle-id) BUNDLE_ID=$2 ;;
        --version) VERSION=$2 ;;
        --build-version) BUILD_VERSION=$2 ;;
        --source-id) SOURCE_ID=$2 ;;
        *) echo "bundle.sh: unknown option $1" >&2; exit 2 ;;
    esac
    shift 2
done
# Values go into an XML plist and, for the version, the public updater.
case "$NAME$EXEC_NAME$BUNDLE_ID$VERSION$BUILD_VERSION$SOURCE_ID" in
    *'<'*|*'>'*|*'&'*|*'"'*|*"'"*) echo "bundle.sh: invalid plist value" >&2; exit 2 ;;
esac
case "$NAME" in ''|.*|*/*) echo "bundle.sh: invalid app name" >&2; exit 2 ;; esac
case "$EXEC_NAME" in ''|.*|*[!A-Za-z0-9._-]*) echo "bundle.sh: invalid executable name" >&2; exit 2 ;; esac
case "$BUNDLE_ID" in ''|*[!A-Za-z0-9.-]*) echo "bundle.sh: invalid bundle identifier" >&2; exit 2 ;; esac
case "$VERSION" in *[!0-9.]*|.*|*.|*..*) echo "bundle.sh: version must contain numeric dot components" >&2; exit 2 ;; esac
APP="$OUT/$NAME.app"
[ -x boot ] || { echo "bundle.sh: runtime/boot missing, run runtime/build.sh first" >&2; exit 1; }
BUILD=$(git rev-list --count HEAD 2>/dev/null || echo 0)
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
if [ -n "$VERSION" ] && [ -z "$BUILD_VERSION" ]; then BUILD_VERSION=$VERSION; fi
VERSION=${VERSION:-0.$BUILD}
BUILD_VERSION=${BUILD_VERSION:-$BUILD ($COMMIT)}
SOURCE_ID=${SOURCE_ID:-$COMMIT}
MINOS=$(otool -l boot | awk '/LC_BUILD_VERSION/ { f = 1 } f && $1 == "minos" { print $2; exit }')

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp boot "$APP/Contents/MacOS/$EXEC_NAME"

# Icon: drawn by app_icon.m (a generic speedometer), every iconset size from the 1024 px master.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
clang -fobjc-arc -w app_icon.m -framework Cocoa -framework ImageIO -framework UniformTypeIdentifiers -o "$TMP/app_icon"
"$TMP/app_icon" "$TMP/icon.png"
mkdir "$TMP/AppIcon.iconset"
for s in 16 32 128 256 512; do
    sips -z $s $s "$TMP/icon.png" --out "$TMP/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2))
    sips -z $d $d "$TMP/icon.png" --out "$TMP/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$TMP/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>              <string>$BUNDLE_ID</string>
    <key>CFBundleName</key>                    <string>$NAME</string>
    <key>CFBundleDisplayName</key>             <string>$NAME</string>
    <key>CFBundleExecutable</key>              <string>$EXEC_NAME</string>
    <key>CFBundleIconFile</key>                <string>AppIcon</string>
    <key>CFBundlePackageType</key>             <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key>   <string>6.0</string>
    <key>CFBundleShortVersionString</key>      <string>$VERSION</string>
    <key>CFBundleVersion</key>                 <string>$BUILD_VERSION</string>
    <key>NFSMWSourceIdentity</key>             <string>$SOURCE_ID</string>
    <key>NSHumanReadableCopyright</key>        <string>Native Apple Silicon runtime for the PC game. Needs your own copy of Need for Speed Most Wanted (PC, v1.3).</string>
    <key>LSMinimumSystemVersion</key>          <string>$MINOS</string>
    <key>LSArchitecturePriority</key>          <array><string>arm64</string></array>
    <key>LSApplicationCategoryType</key>       <string>public.app-category.racing-games</string>
    <key>LSSupportsGameMode</key>              <true/>
    <key>NSHighResolutionCapable</key>         <true/>
    <key>NSPrincipalClass</key>                <string>NSApplication</string>
    <key>NSSupportsAutomaticTermination</key>  <false/>
    <key>NSSupportsSuddenTermination</key>     <false/>
    <key>GCSupportsControllerUserInteraction</key> <true/>
    <key>GCSupportedGameControllers</key>
    <array><dict><key>ProfileName</key><string>ExtendedGamepad</string></dict></array>
</dict>
</plist>
EOF
plutil -lint -s "$APP/Contents/Info.plist"
codesign --force --sign - --timestamp=none "$APP"
echo "bundle.sh: $APP (build $BUILD, $COMMIT, macOS $MINOS+)"
