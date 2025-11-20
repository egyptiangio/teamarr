#!/bin/bash
# Version bump script for Teamarr
# Usage: ./scripts/bump_version.sh [major|minor|patch|custom]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$ROOT_DIR/config.py"

# Get current version
CURRENT_VERSION=$(grep "^VERSION = " "$CONFIG_FILE" | sed 's/VERSION = "\(.*\)"/\1/')

echo "Current version: $CURRENT_VERSION"

# Parse version
IFS='.-' read -r MAJOR MINOR PATCH SUFFIX <<< "$CURRENT_VERSION"

case "${1:-patch}" in
    major)
        MAJOR=$((MAJOR + 1))
        MINOR=0
        PATCH=0
        ;;
    minor)
        MINOR=$((MINOR + 1))
        PATCH=0
        ;;
    patch)
        PATCH=$((PATCH + 1))
        ;;
    custom)
        if [ -z "$2" ]; then
            echo "Error: Please provide version number"
            echo "Usage: $0 custom X.Y.Z"
            exit 1
        fi
        NEW_VERSION="$2"
        ;;
    *)
        echo "Usage: $0 [major|minor|patch|custom VERSION]"
        exit 1
        ;;
esac

# Build new version
if [ "$1" != "custom" ]; then
    if [ -n "$SUFFIX" ]; then
        NEW_VERSION="$MAJOR.$MINOR.$PATCH-$SUFFIX"
    else
        NEW_VERSION="$MAJOR.$MINOR.$PATCH"
    fi
fi

echo "New version: $NEW_VERSION"

# Update config.py
sed -i "s/^VERSION = \".*\"/VERSION = \"$NEW_VERSION\"/" "$CONFIG_FILE"

echo "✅ Updated config.py"

# Commit the change
if command -v git &> /dev/null && git rev-parse --git-dir > /dev/null 2>&1; then
    read -p "Commit version bump? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        git add "$CONFIG_FILE"
        git commit -m "Bump version to $NEW_VERSION"
        echo "✅ Committed version bump"

        read -p "Create git tag v$NEW_VERSION? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION"
            echo "✅ Created tag v$NEW_VERSION"
            echo "Run 'git push origin v$NEW_VERSION' to push the tag"
        fi
    fi
fi

echo "✅ Version bumped: $CURRENT_VERSION → $NEW_VERSION"
