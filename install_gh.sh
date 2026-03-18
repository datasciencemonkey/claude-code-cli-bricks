#!/bin/bash
set -e -u

# Install the latest GitHub CLI (gh) 2.x release to ~/.local/bin
# and create a wrapper that handles `gh auth login` for xterm.js PTY compatibility.
#
# Usage: bash install_gh.sh

INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "${INSTALL_DIR}"

# --- Detect latest 2.x version from GitHub API ---
latest_json="$(curl -fsSL 'https://api.github.com/repos/cli/cli/releases/latest' 2>/dev/null)" || true

GH_VERSION=""
if [ -n "${latest_json:-}" ]; then
    GH_VERSION="$(echo "${latest_json}" | grep -oEm1 '"tag_name":\s*"v(2\.[0-9]+\.[0-9]+)"' | grep -oE '2\.[0-9]+\.[0-9]+' || true)"
fi

if [ -z "${GH_VERSION}" ]; then
    echo "ERROR: Could not detect latest gh 2.x release from GitHub API." >&2
    echo "Check your network connection or GitHub API rate limits." >&2
    exit 1
fi

echo "Installing gh v${GH_VERSION}..."

# --- Download and extract ---
TARBALL="/tmp/gh.tar.gz"
curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" -o "${TARBALL}"
tar -xzf "${TARBALL}" -C /tmp
mv "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" "${INSTALL_DIR}/gh"
rm -rf "${TARBALL}" "/tmp/gh_${GH_VERSION}_linux_amd64"
chmod +x "${INSTALL_DIR}/gh"

# --- Configure git protocol ---
"${INSTALL_DIR}/gh" config set git_protocol https 2>/dev/null || true

# --- Create wrapper script ---
# The wrapper intercepts `gh auth login` to pipe "Y" through the interactive
# prompt, which avoids arrow-key menus that break in xterm.js PTY sessions.
cat > "${INSTALL_DIR}/gh.wrapper" << 'WRAPPER'
#!/bin/bash
if [ "${1:-}" = "auth" ] && [ "${2:-}" = "login" ]; then
    shift 2
    printf "Y\n" | ~/.local/bin/gh.real auth login -h github.com -p https -w --skip-ssh-key "$@"
    exit 0
fi
exec ~/.local/bin/gh.real "$@"
WRAPPER

mv "${INSTALL_DIR}/gh" "${INSTALL_DIR}/gh.real"
mv "${INSTALL_DIR}/gh.wrapper" "${INSTALL_DIR}/gh"
chmod +x "${INSTALL_DIR}/gh"

echo "gh v${GH_VERSION} installed to ${INSTALL_DIR}/gh"
