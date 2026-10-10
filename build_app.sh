#!/bin/sh
# Builds JevTalk.app, a native window around jevtalk_app.py. Needs Xcode or the Command Line Tools
# (xcode-select --install). Builds vocab.txt first if needed, which needs wordfreq (see build_vocab.py).
#
#   ./build_app.sh            # -> build/JevTalk.app
#   ./build_app.sh --install  # also copies it to ~/Applications
set -e
cd "$(dirname "$0")"
if [ ! -f vocab.txt ]; then
  python3 build_vocab.py || { echo "Couldn't build vocab.txt. Install wordfreq first: pip install wordfreq==3.1.1"; exit 1; }
fi

APP=build/JevTalk.app
rm -rf build/JevTalk.app build/JevTalk.iconset
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" build/JevTalk.iconset

# The app runs its own copy of the Python side, so it keeps working wherever this folder lives.
cp -R jevtalk.py jevtalk_app.py vocab.txt app "$APP/Contents/Resources/"

xcrun swiftc -O -target "$(uname -m)-apple-macos13.0" -sdk "$(xcrun --show-sdk-path)" \
  JevTalkShell.swift -o "$APP/Contents/MacOS/JevTalk"

for s in 16 32 128 256 512; do
  sips -z $s $s jevtalk_icon.png --out "build/JevTalk.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) jevtalk_icon.png --out "build/JevTalk.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns build/JevTalk.iconset -o "$APP/Contents/Resources/JevTalk.icns"

cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>JevTalk</string>
  <key>CFBundleDisplayName</key><string>JevTalk</string>
  <key>CFBundleIdentifier</key><string>us.promptable.jevtalk</string>
  <key>CFBundleExecutable</key><string>JevTalk</string>
  <key>CFBundleIconFile</key><string>JevTalk</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
EOF
codesign --force --sign - "$APP"  # ad-hoc signature, enough to run on your own Mac
echo "Built $APP"

if [ "$1" = "--install" ]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/JevTalk.app"
  cp -R "$APP" "$HOME/Applications/"
  echo "Installed to ~/Applications/JevTalk.app"
fi
