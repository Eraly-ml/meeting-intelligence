#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
CHECK_DIR="$PWD/build/core-checks"
mkdir -p "$CHECK_DIR/modules"
MAC_ARCH="$(uname -m)"
swiftc -swift-version 5 -target "$MAC_ARCH-apple-macosx15.0" -module-cache-path "$CHECK_DIR/modules" \
    macos/Sources/MeetingBox/Models.swift macos/Sources/MeetingBox/MeetingRepository.swift \
    scripts/RepositoryChecks.swift -o "$CHECK_DIR/repository"
"$CHECK_DIR/repository"
swiftc -swift-version 5 -target "$MAC_ARCH-apple-macosx15.0" -module-cache-path "$CHECK_DIR/modules" \
    macos/Sources/MeetingBox/Models.swift macos/Sources/MeetingBox/HubClient.swift \
    scripts/HubChecks.swift -o "$CHECK_DIR/hub"
"$CHECK_DIR/hub"
swiftc -swift-version 5 -target "$MAC_ARCH-apple-macosx15.0" -module-cache-path "$CHECK_DIR/modules" \
    macos/Sources/MeetingBox/Audio/*.swift scripts/AudioChecks.swift -o "$CHECK_DIR/audio"
"$CHECK_DIR/audio"
