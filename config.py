"""
Teamarr - Sports Team EPG Generator Configuration
"""
import subprocess
import os

# Application version - single source of truth
# Format: MAJOR.MINOR.PATCH[-pre-release][+build]
BASE_VERSION = "0.2.0"

def get_version():
    """
    Get version string with automatic dev/branch detection

    Returns:
        - "X.Y.Z" on main/master branch (stable release)
        - "X.Y.Z-dev" on dev branch (development version)
        - "X.Y.Z-dev+SHA" on dev branch with commit SHA (if git available)
    """
    version = BASE_VERSION

    try:
        # Try to get current git branch
        git_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.git')
        if os.path.exists(git_dir):
            branch = subprocess.check_output(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                stderr=subprocess.DEVNULL,
                text=True
            ).strip()

            # Add suffix for non-main branches
            if branch == 'dev':
                # Get short commit SHA
                try:
                    sha = subprocess.check_output(
                        ['git', 'rev-parse', '--short', 'HEAD'],
                        stderr=subprocess.DEVNULL,
                        text=True
                    ).strip()
                    version = f"{BASE_VERSION}-dev+{sha}"
                except:
                    version = f"{BASE_VERSION}-dev"
            elif branch not in ['main', 'master']:
                # Other branches (feature/fix/etc)
                version = f"{BASE_VERSION}-{branch}"
    except:
        # Git not available or error - return base version
        pass

    return version

VERSION = get_version()

# Application settings
APP_NAME = "Teamarr"
APP_DESCRIPTION = "Sports Team EPG Generator for IPTV"

# Default settings
DEFAULT_DAYS_AHEAD = 14
DEFAULT_UPDATE_TIME = "00:00"
DEFAULT_AUTO_GENERATE_FREQUENCY = "daily"
