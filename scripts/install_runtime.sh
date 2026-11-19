#!/usr/bin/env bash
# User-scoped runtime for install.sh; sudo is used only for Ubuntu apt packages.
set -euo pipefail

readonly NODE_VERSION="22.23.3"
readonly PNPM_VERSION="11.10.0"
readonly MOLTEMPLATE_VERSION="2.22.5"
readonly WRAPPER_MARKER="# mdagent-install-runtime:lmp_mpi:v1"
DATA_ROOT=""
ENGINE="lammps"
SKIP_APT=0
PLAN=0

die() { printf '[runtime] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[runtime] %s\n' "$*"; }
usage() {
    printf '%s\n' \
        'Usage: bash scripts/install_runtime.sh --data-root /absolute/path [options]' \
        '  --engine lammps|both|none  Default: lammps; both also installs GROMACS.' \
        '  --skip-apt                Reuse already installed system dependencies.' \
        '  --plan                    Print the plan; no writes, network or apt calls.' \
        '  --help                    Show this help.' \
        'Packmol and moltemplate are modelling tools installed for every engine choice.'
}

while (($#)); do
    case "$1" in
        --data-root)
            (($# >= 2)) || die '--data-root requires a path'
            DATA_ROOT="$2"; shift 2 ;;
        --engine)
            (($# >= 2)) || die '--engine requires lammps, both or none'
            ENGINE="$2"; shift 2 ;;
        --skip-apt) SKIP_APT=1; shift ;;
        --plan) PLAN=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done
[[ "$ENGINE" == lammps || "$ENGINE" == both || "$ENGINE" == none ]] || \
    die '--engine must be lammps, both or none'
[[ "$DATA_ROOT" == /* && "$DATA_ROOT" != / ]] || \
    die '--data-root must be an absolute directory other than /'
[[ "$DATA_ROOT" != *$'\n'* && "$DATA_ROOT" != *$'\r'* ]] || \
    die '--data-root must not contain newlines'
DATA_ROOT="${DATA_ROOT%/}"
[[ -n "$DATA_ROOT" ]] || die '--data-root must not be /'

APT_PACKAGES=(ca-certificates git curl xz-utils build-essential cmake gfortran
              python3 python3-venv python3-dev openmpi-bin libopenmpi-dev packmol)
if [[ "$ENGINE" != none ]]; then APT_PACKAGES+=(lammps); fi
if [[ "$ENGINE" == both ]]; then APT_PACKAGES+=(gromacs); fi

# Deliberately exit before OS probing, command substitution, mkdir or downloads.
# A plan can be inspected on another OS and by root without changing that machine.
if ((PLAN)); then
    log 'PLAN ONLY: no filesystem changes, network access or apt execution'
    log 'Target: Ubuntu 22.04/24.04, non-root user, x86_64 or aarch64'
    if ((SKIP_APT)); then
        log "Skip apt; require existing system packages: ${APT_PACKAGES[*]}"
    else
        log 'sudo apt-get update'
        log "sudo apt-get install --no-install-recommends -y ${APT_PACKAGES[*]}"
    fi
    log "Node $NODE_VERSION: official nodejs.org HTTPS tar.xz, pinned SHA256 verification"
    log "Node executable: $DATA_ROOT/runtime/node/bin/node"
    log "npm install --global --prefix $DATA_ROOT/runtime/npm-tools pnpm@$PNPM_VERSION"
    log "pnpm executable: $DATA_ROOT/runtime/npm-tools/bin/pnpm"
    log "python3 (>=3.10) -m venv $DATA_ROOT/runtime/venv"
    log 'venv pip install -r requirements-install.txt'
    log "venv pip install moltemplate==$MOLTEMPLATE_VERSION"
    log "Python executable: $DATA_ROOT/runtime/venv/bin/python"
    if [[ "$ENGINE" != none ]]; then
        log 'Check actual LAMMPS -help output for exact reaxff and qeq/reaxff styles'
        log "Create $DATA_ROOT/bin/lmp_mpi wrapper; refuse to overwrite unknown files"
    fi
    if [[ "$ENGINE" == both ]]; then log 'Check gmx --version'; fi
    log "PATH directories: $DATA_ROOT/bin:$DATA_ROOT/runtime/node/bin:$DATA_ROOT/runtime/npm-tools/bin:$DATA_ROOT/runtime/venv/bin"
    exit 0
fi

((EUID != 0)) || die 'Run as your ordinary Ubuntu user; do not sudo the installer'
[[ -r /etc/os-release ]] || die 'Cannot identify Ubuntu (/etc/os-release missing)'
# /etc/os-release is trusted OS metadata, not user configuration or credentials.
. /etc/os-release
[[ "${ID:-}" == ubuntu && ("${VERSION_ID:-}" == 22.04 || "${VERSION_ID:-}" == 24.04) ]] || \
    die 'Supported systems: Ubuntu 22.04 and Ubuntu 24.04'

DATA_ROOT="$(realpath -m -- "$DATA_ROOT")"
[[ "$DATA_ROOT" != / ]] || die 'Resolved data root must not be /'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CODE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
REQUIREMENTS="$CODE_ROOT/requirements-install.txt"
[[ -f "$REQUIREMENTS" ]] || die "Missing dependency manifest: $REQUIREMENTS"
RUNTIME="$DATA_ROOT/runtime"
NODE_ROOT="$RUNTIME/node"
NPM_ROOT="$RUNTIME/npm-tools"
VENV_ROOT="$RUNTIME/venv"
BIN_ROOT="$DATA_ROOT/bin"

# Never install through a runtime/bin symlink or replace someone else's wrapper.
for dir in "$RUNTIME" "$NODE_ROOT" "$NPM_ROOT" "$VENV_ROOT" "$BIN_ROOT"; do
    [[ ! -L "$dir" ]] || die "Refusing symlink runtime directory: $dir"
    if [[ -e "$dir" ]]; then
        [[ -d "$dir" && -O "$dir" && -w "$dir" ]] || \
            die "Runtime directory must be writable and owned by you: $dir"
    fi
done
if [[ "$ENGINE" != none && (-e "$BIN_ROOT/lmp_mpi" || -L "$BIN_ROOT/lmp_mpi") ]]; then
    [[ ! -L "$BIN_ROOT/lmp_mpi" && -f "$BIN_ROOT/lmp_mpi" ]] && \
        grep -Fxq -- "$WRAPPER_MARKER" "$BIN_ROOT/lmp_mpi" || \
        die "Preserving unknown $BIN_ROOT/lmp_mpi; choose another data root or move it yourself"
fi

case "$(uname -m)" in
    x86_64) NODE_ARCH=x64
        NODE_SHA256=df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de ;;
    aarch64|arm64) NODE_ARCH=arm64
        NODE_SHA256=a44aeb94849a299b22df10b9e622ec2f605c2183501bc40590705131de7c740f ;;
    *) die 'Supported CPU architectures: x86_64 and aarch64' ;;
esac
# Hashes published in https://nodejs.org/en/blog/release/v22.23.3 (SHASUMS).

if ((!SKIP_APT)); then
    command -v sudo >/dev/null || die 'sudo is required for apt (or use --skip-apt)'
    log 'Installing Ubuntu system packages (sudo only for apt)'
    sudo apt-get update
    sudo apt-get install --no-install-recommends -y "${APT_PACKAGES[@]}"
fi

for cmd in git curl tar xz sha256sum python3 make cmake gfortran mpirun mpicc packmol timeout; do
    command -v "$cmd" >/dev/null || die "Required command missing: $cmd (install the system dependencies)"
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || \
    die 'Ubuntu python3 >=3.10 is required'

LAMMPS_BIN=""
if [[ "$ENGINE" != none ]]; then
    # -help only reports compiled capabilities; no simulation/job is started.
    while IFS= read -r candidate; do
        [[ -x "$candidate" ]] || continue
        [[ "$(realpath -- "$candidate")" != "$BIN_ROOT/lmp_mpi" ]] || continue
        if help_text="$(OMP_NUM_THREADS=1 timeout 30 "$candidate" -help 2>&1)" && \
           grep -Eq '(^|[[:space:]])reaxff([[:space:]]|$)' <<<"$help_text" && \
           grep -Eq '(^|[[:space:]])qeq/reaxff([[:space:]]|$)' <<<"$help_text"; then
            LAMMPS_BIN="$(realpath -- "$candidate")"
            break
        fi
    done < <(type -aP lmp_mpi lmp 2>/dev/null || true)
    [[ -n "$LAMMPS_BIN" ]] || die \
        'No usable LAMMPS with reaxff and qeq/reaxff. Ubuntu lammps may omit these styles. Install a REAXFF-enabled MPI build manually following docs/迁移安装指南_UBUNTU.md, put its lmp/lmp_mpi on PATH, then rerun with --skip-apt. Installation is incomplete.'
    log "LAMMPS capability check passed: $LAMMPS_BIN (reaxff + qeq/reaxff)"
fi
if [[ "$ENGINE" == both ]]; then
    command -v gmx >/dev/null || die 'GROMACS gmx command missing'
    gmx --version >/dev/null 2>&1 || die 'GROMACS gmx --version failed'
fi

mkdir -p -- "$RUNTIME" "$BIN_ROOT" "$NPM_ROOT"
WORK_DIR=""
cleanup() {
    if [[ -n "$WORK_DIR" && "$WORK_DIR" == "$RUNTIME"/.node-install.* && -d "$WORK_DIR" && ! -L "$WORK_DIR" ]]; then
        rm -rf -- "$WORK_DIR"
    fi
}
trap cleanup EXIT

if [[ -e "$NODE_ROOT" ]]; then
    [[ -x "$NODE_ROOT/bin/node" && -x "$NODE_ROOT/bin/npm" ]] || \
        die "Existing $NODE_ROOT is not a usable Node runtime; left unchanged"
    [[ "$("$NODE_ROOT/bin/node" --version)" == "v$NODE_VERSION" ]] || \
        die "Existing $NODE_ROOT is not Node $NODE_VERSION; left unchanged"
    log "Reusing Node $NODE_VERSION in $NODE_ROOT"
else
    WORK_DIR="$(mktemp -d "$RUNTIME/.node-install.XXXXXXXX")"
    archive="node-v$NODE_VERSION-linux-$NODE_ARCH.tar.xz"
    log "Downloading Node $NODE_VERSION from nodejs.org"
    curl --fail --show-error --silent --location --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --retry 3 --connect-timeout 20 --max-time 600 \
        "https://nodejs.org/dist/v$NODE_VERSION/$archive" --output "$WORK_DIR/$archive"
    printf '%s  %s\n' "$NODE_SHA256" "$WORK_DIR/$archive" | \
        sha256sum --check --strict --status || die 'Node archive SHA256 mismatch; nothing extracted'
    mkdir -- "$WORK_DIR/node"
    tar --extract --xz --file "$WORK_DIR/$archive" --directory "$WORK_DIR/node" \
        --strip-components=1 --no-same-owner
    [[ "$("$WORK_DIR/node/bin/node" --version)" == "v$NODE_VERSION" ]] || die 'Downloaded Node version check failed'
    mv -- "$WORK_DIR/node" "$NODE_ROOT"
fi
export PATH="$NODE_ROOT/bin:$NPM_ROOT/bin:$PATH"

if [[ -e "$NPM_ROOT/bin/pnpm" || -L "$NPM_ROOT/bin/pnpm" ]]; then
    [[ -x "$NPM_ROOT/bin/pnpm" && "$("$NPM_ROOT/bin/pnpm" --version)" == "$PNPM_VERSION" ]] || \
        die "Existing pnpm in $NPM_ROOT is not version $PNPM_VERSION; left unchanged"
else
    [[ ! -e "$NPM_ROOT/bin/pnpx" && ! -L "$NPM_ROOT/bin/pnpx" ]] || \
        die "Preserving unknown $NPM_ROOT/bin/pnpx"
    log "Installing pnpm $PNPM_VERSION in user prefix $NPM_ROOT"
    "$NODE_ROOT/bin/npm" install --global --prefix "$NPM_ROOT" \
        --cache "$RUNTIME/npm-cache" --registry https://registry.npmjs.org \
        --no-audit --no-fund "pnpm@$PNPM_VERSION"
fi
[[ "$("$NPM_ROOT/bin/pnpm" --version)" == "$PNPM_VERSION" ]] || die 'pnpm version verification failed'

if [[ -e "$VENV_ROOT" ]]; then
    [[ -f "$VENV_ROOT/pyvenv.cfg" && -x "$VENV_ROOT/bin/python" ]] || \
        die "Existing $VENV_ROOT is not a Python virtual environment; left unchanged"
else
    log "Creating Python virtual environment: $VENV_ROOT"
    python3 -m venv "$VENV_ROOT" || die 'venv creation failed; install Ubuntu python3-venv'
fi
"$VENV_ROOT/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.prefix != sys.base_prefix else 1)' || \
    die 'The runtime venv must use Python >=3.10'
log 'Installing Python dependencies into the runtime venv'
"$VENV_ROOT/bin/python" -m pip install --disable-pip-version-check --require-virtualenv \
    --index-url https://pypi.org/simple -r "$REQUIREMENTS"
# Confirmed release: https://pypi.org/project/moltemplate/2.22.5/
"$VENV_ROOT/bin/python" -m pip install --disable-pip-version-check --require-virtualenv \
    --index-url https://pypi.org/simple "moltemplate==$MOLTEMPLATE_VERSION"
"$VENV_ROOT/bin/python" -m pip check
[[ -x "$VENV_ROOT/bin/moltemplate.sh" ]] || die 'moltemplate.sh entry point missing from venv'

if [[ "$ENGINE" != none ]]; then
    wrapper_tmp="$(mktemp "$BIN_ROOT/.lmp_mpi.XXXXXXXX")"
    {
        printf '#!/usr/bin/env bash\n%s\n' "$WRAPPER_MARKER"
        printf 'exec %q "$@"\n' "$LAMMPS_BIN"
    } > "$wrapper_tmp"
    chmod 755 -- "$wrapper_tmp"
    mv -- "$wrapper_tmp" "$BIN_ROOT/lmp_mpi"
fi
log "Runtime ready: Node $NODE_VERSION, pnpm $PNPM_VERSION, Python venv, moltemplate $MOLTEMPLATE_VERSION"
log "PATH directories: $BIN_ROOT:$NODE_ROOT/bin:$NPM_ROOT/bin:$VENV_ROOT/bin"
