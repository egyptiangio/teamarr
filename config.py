"""
Teamarr - Sports Team EPG Generator Configuration
"""
import subprocess
import os

# Application version - single source of truth
# Format: MAJOR.MINOR.PATCH[-pre-release][+build]
BASE_VERSION = "0.3.0"

def get_version():
    """
    Get version string with automatic dev/branch detection

    Returns:
        - "X.Y.Z" on main/master branch (stable release)
        - "X.Y.Z-dev" on dev branch (development version)
        - "X.Y.Z-dev+SHA" on dev branch with commit SHA (if git available)
    """
    version = BASE_VERSION
    branch = None
    sha = None

    # First, try to read from Docker build-time files (created in Dockerfile)
    try:
        branch_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.git-branch')
        sha_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.git-sha')

        if os.path.exists(branch_file):
            with open(branch_file, 'r') as f:
                branch = f.read().strip()

        if os.path.exists(sha_file):
            with open(sha_file, 'r') as f:
                sha = f.read().strip()
    except:
        pass

    # Fallback to environment variables (also set in Dockerfile)
    if not branch:
        branch = os.environ.get('GIT_BRANCH')
    if not sha:
        sha = os.environ.get('GIT_SHA')

    # Last fallback: try git commands (for development)
    if not branch or branch == 'unknown':
        try:
            git_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.git')
            if os.path.exists(git_dir):
                branch = subprocess.check_output(
                    ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                    stderr=subprocess.DEVNULL,
                    text=True
                ).strip()

                if not sha or sha == 'unknown':
                    try:
                        sha = subprocess.check_output(
                            ['git', 'rev-parse', '--short', 'HEAD'],
                            stderr=subprocess.DEVNULL,
                            text=True
                        ).strip()
                    except:
                        pass
        except:
            pass

    # Build version string
    if branch and branch != 'unknown':
        if branch == 'dev':
            if sha and sha != 'unknown':
                version = f"{BASE_VERSION}-dev+{sha}"
            else:
                version = f"{BASE_VERSION}-dev"
        elif branch not in ['main', 'master']:
            version = f"{BASE_VERSION}-{branch}"

    return version

VERSION = get_version()

# Application settings
APP_NAME = "Teamarr"
APP_DESCRIPTION = "Sports Team EPG Generator for IPTV"

# Default settings
DEFAULT_DAYS_AHEAD = 14
DEFAULT_UPDATE_TIME = "00:00"
DEFAULT_AUTO_GENERATE_FREQUENCY = "daily"
