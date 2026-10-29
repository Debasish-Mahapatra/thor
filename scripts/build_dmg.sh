#!/bin/bash
# Builds a self-contained Thor.app (its own Python, packages and interface) and build/Thor.dmg.
# The app needs nothing installed on the Mac it runs on: on first launch it installs Claude Code and
# MacTeX by itself, and macOS asks for the password once.
set -euo pipefail
cd "$(dirname "$0")/.."

UV=${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}
NAME=$(/usr/libexec/PlistBuddy -c "Print :CFBundleName" shell/Info.plist)
WORK=build/dist
APP=$WORK/$NAME.app
RES=$APP/Contents/Resources

rm -rf "$WORK" "build/$NAME.dmg"
mkdir -p "$APP/Contents/MacOS" "$RES"

echo "Python: a relocatable CPython 3.12 (python-build-standalone, through uv)"
"$UV" python install 3.12 --install-dir build/python-dist --no-bin >/dev/null
PYSRC=$(ls -d build/python-dist/cpython-3.12.*-macos-aarch64-none | tail -1)
cp -R "$PYSRC" "$RES/python"
PY=$RES/python/bin/python3.12
STDLIB=$RES/python/lib/python3.12
rm -f "$STDLIB/EXTERNALLY-MANAGED"
rm -rf "$STDLIB/test" "$STDLIB/idlelib" "$STDLIB/tkinter" "$STDLIB/turtledemo" "$RES/python/include"

echo "Packages: the versions in backend/uv.lock, then Thor's backend"
(cd backend && "$UV" export --frozen --no-dev --no-emit-project --no-hashes -o ../build/requirements.txt >/dev/null)
"$UV" pip install --quiet --python "$PY" -r build/requirements.txt
"$UV" pip install --quiet --python "$PY" --no-deps ./backend
# Thor runs the Claude Code that first-run setup installs, so the SDK's own copy (270 MB) is left out.
rm -rf "$STDLIB/site-packages/claude_agent_sdk/_bundled"
# Compiled now, so Python never writes into the signed bundle.
"$PY" -m compileall -q "$STDLIB" >/dev/null || true

echo "Interface and shell"
make --no-print-directory ui/dist/index.html build/AppIcon.icns >/dev/null
cp -R ui/dist "$RES/ui"
(cd shell && swift build -c release -q)
cp shell/.build/release/Thor "$APP/Contents/MacOS/Thor"
cp shell/Info.plist "$APP/Contents/Info.plist"
cp build/AppIcon.icns "$RES/AppIcon.icns"
# Paths relative to Contents/Resources.
printf '{"python": "python/bin/python3.12", "workdir": ".", "ui": "ui"}\n' > "$RES/backend.json"
codesign --force --sign - "$APP"

echo "Disk image"
STAGE=$WORK/dmg
mkdir -p "$STAGE"
mv "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "$NAME" -srcfolder "$STAGE" -ov -format UDZO "build/$NAME.dmg"
rm -rf "$WORK"
echo "Built build/$NAME.dmg ($(du -h "build/$NAME.dmg" | cut -f1))"
