#!/bin/sh
set -eu
repository='dat3133/audio2text-cli'
version='0.1.0-beta.1'
install_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
no_init=0
artifact_path=''
checksum_path=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --repository|--version|--install-dir|--artifact-path|--checksum-path)
            [ "$#" -ge 2 ] && [ -n "$2" ] || { echo "Missing value for $1" >&2; exit 2; }
            case "$1" in
                --repository) repository="$2" ;;
                --version) version="$2" ;;
                --install-dir) install_dir="$2" ;;
                --artifact-path) artifact_path="$2" ;;
                --checksum-path) checksum_path="$2" ;;
            esac
            shift 2 ;;
        --no-init) no_init=1; shift ;;
        *) echo "Unknown installer option: $1" >&2; exit 2 ;;
    esac
done
[ "$(uname -s)" = 'Linux' ] && [ "$(uname -m)" = 'x86_64' ] || { echo 'Only Linux x86_64 is supported.' >&2; exit 3; }
printf '%s' "$repository" | grep -Eq '^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$' || { echo 'Invalid --repository; expected owner/repository.' >&2; exit 2; }
printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?$' || exit 2
command -v sha256sum >/dev/null || { echo 'Installer requires sha256sum.' >&2; exit 3; }
mkdir -p "$install_dir"
asset='audio2text-linux-x86_64'
base="https://github.com/$repository/releases/download/v$version"
partial="$install_dir/.audio2text.$$.partial"
manifest="$install_dir/.audio2text.$$.SHA256SUMS"
if [ -n "$artifact_path" ]; then
    [ -n "$checksum_path" ] || { echo 'Local artifacts require --checksum-path.' >&2; exit 2; }
    cp "$checksum_path" "$manifest"
    cp "$artifact_path" "$partial"
else
    command -v curl >/dev/null || { echo 'Installer requires curl.' >&2; exit 3; }
    curl --proto '=https' --tlsv1.2 --retry 2 --connect-timeout 30 --max-time 180 -fsSL "$base/SHA256SUMS" -o "$manifest"
    curl --proto '=https' --tlsv1.2 --retry 2 --connect-timeout 30 --max-time 180 -fsSL "$base/$asset" -o "$partial"
fi
expected=$(awk -v name="$asset" '$2 == name {count++; value=$1} END {if(count==1) print value}' "$manifest")
[ "${#expected}" = 64 ] || { echo 'Missing SHA256.' >&2; exit 6; }
actual=$(sha256sum "$partial" | cut -d ' ' -f 1)
[ "$actual" = "$expected" ] || { echo 'SHA256 mismatch; partial retained.' >&2; exit 6; }
chmod 755 "$partial"
candidate_version=$("$partial" --version)
[ "$candidate_version" = "audio2text $version" ] || { echo 'Downloaded binary version mismatch; previous installation retained.' >&2; exit 3; }
mv "$partial" "$install_dir/audio2text"
installed_version=$("$install_dir/audio2text" --version)
[ "$installed_version" = "audio2text $version" ] || { echo 'Installed binary version mismatch.' >&2; exit 3; }
printf '%s\n' "$installed_version"
echo "CLI installed: $install_dir/audio2text" >&2
case ":$PATH:" in *":$install_dir:"*) ;; *) echo "Add $install_dir to PATH." >&2 ;; esac
if [ "$no_init" = 0 ] && [ -t 2 ] && ( : </dev/tty ) 2>/dev/null; then
    "$install_dir/audio2text" init </dev/tty || { code=$?; echo 'CLI installed; initialization incomplete. Run audio2text init again.' >&2; exit "$code"; }
else
    echo 'Run audio2text init; for automation use init --non-interactive.' >&2
fi
