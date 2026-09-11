#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
swift build --package-path macos --configuration release --scratch-path "$PWD/build/swift" --disable-sandbox
APP="$PWD/build/MeetingBox.app"
mkdir -p "$APP/Contents/MacOS"
cp build/swift/release/MeetingBox "$APP/Contents/MacOS/MeetingBox"
cp macos/Info.plist "$APP/Contents/Info.plist"
codesign --force --sign - "$APP"
printf 'Built %s\n' "$APP"
