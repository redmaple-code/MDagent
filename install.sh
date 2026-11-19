#!/usr/bin/env bash
# Ubuntu single-user deployment. Run as your regular user, not with sudo.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DATA_ROOT="$(dirname -- "$HERE")/mdsim-data"
KIT_SOURCE="$(dirname -- "$HERE")/agent_deploy_kit"
CASE_ROOT=""
ENGINE=both
PLAN=0
CHECK=0
SKIP_APT=0
NO_START=0
NON_INTERACTIVE=0
RECONFIGURE=0
MODEL_ARGS=()
die() { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'HELP'
MDagent Ubuntu installer (22.04/24.04, regular user)
Usage: bash install.sh --kit /path/to/company-kit [options]
  --data-root PATH       Runtime/data directory (default: ../mdsim-data)
  --run-case-base PATH   New simulation workspace (default: DATA/projects)
  --engine both|lammps|none  Default: both; none prepares agent tools only
  --provider deepseek|openai-compatible|custom
  --model ID --base-url URL --api API  Optional model routing overrides
  --non-interactive      Read Key from MDAGENT_MODEL_API_KEY; never an argument
  --reconfigure-model    Explicitly change an existing model configuration
  --skip-apt             Reuse installed Ubuntu system dependencies
  --no-start             Generate services without starting them
  --plan                Read-only preview; no downloads or file changes
  --check               Check an existing installation; no model or job calls
HELP
}
while (($#)); do
  case "$1" in
    --kit|--data-root|--run-case-base|--engine)
      (($# >= 2)) || die "$1 needs a value"
      case "$1" in
        --kit) KIT_SOURCE="$2" ;;
        --data-root) DATA_ROOT="$2" ;;
        --run-case-base) CASE_ROOT="$2" ;;
        --engine) ENGINE="$2" ;;
      esac
      shift 2 ;;
    --provider|--model|--base-url|--api|--context-window|--max-tokens)
      (($# >= 2)) || die "$1 needs a value"
      MODEL_ARGS+=("$1" "$2"); shift 2 ;;
    --non-interactive) NON_INTERACTIVE=1; shift ;;
    --reconfigure-model) RECONFIGURE=1; shift ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --no-start) NO_START=1; shift ;;
    --plan) PLAN=1; shift ;;
    --check) CHECK=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[[ "$ENGINE" == both || "$ENGINE" == lammps || "$ENGINE" == none ]] || die 'Unknown engine'
for path in "$DATA_ROOT" "$KIT_SOURCE" "${CASE_ROOT:-$DATA_ROOT/projects}"; do
  [[ "$path" == /* && "$path" != / && "$path" != *$'\n'* && "$path" != *$'\r'* ]] || \
    die 'Use absolute paths other than /, without line breaks'
done
CASE_ROOT="${CASE_ROOT:-$DATA_ROOT/projects}"
RUNTIME_ARGS=(--data-root "$DATA_ROOT" --engine "$ENGINE")
((!SKIP_APT)) || RUNTIME_ARGS+=(--skip-apt)
if ((PLAN)); then
  printf '[install] Read-only plan\nCode: %s\nSource kit: %s\nData: %s\nCases: %s\n' "$HERE" "$KIT_SOURCE" "$DATA_ROOT" "$CASE_ROOT"
  bash "$HERE/scripts/install_runtime.sh" "${RUNTIME_ARGS[@]}" --plan
  printf '%s\n' \
    'Prepare a code-only kit copy; keep the source kit untouched.' \
    'Configure local single-user paths, preserving existing model credentials.' \
    'Install dsh profiles, build native dependencies, and require passing regressions.' \
    'Ask for provider/model and a private API Key (or environment input).' \
    'Generate user services bound to 127.0.0.1; retain existing simulation state.' \
    'Check dependencies/configuration; model dialogue and scientific jobs require separate acceptance.'
  exit 0
fi
command -v python3 >/dev/null || die 'Ubuntu python3 is required'
if ((CHECK)); then
  CHECK_PY="$DATA_ROOT/runtime/venv/bin/python"
  [[ -x "$CHECK_PY" ]] || CHECK_PY="$(command -v python3)"
  exec "$CHECK_PY" "$HERE/scripts/install_check.py" --data-root "$DATA_ROOT"
fi
((EUID != 0)) || die 'Run as your regular Ubuntu user; sudo is only used for apt'
[[ -r /etc/os-release ]] || die 'Ubuntu OS metadata missing'
. /etc/os-release
[[ "${ID:-}" == ubuntu && ("${VERSION_ID:-}" == 22.04 || "${VERSION_ID:-}" == 24.04) ]] || die 'Supported systems: Ubuntu 22.04/24.04'
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die 'Python >=3.10 required'
DATA_ROOT="$(realpath -m -- "$DATA_ROOT")"
CASE_ROOT="$(realpath -m -- "$CASE_ROOT")"
KIT_SOURCE="$(realpath -m -- "$KIT_SOURCE")"
KIT_DEST="$DATA_ROOT/runtime/kit"
MPI_BIN=/usr/bin/mpirun
if ((SKIP_APT)); then MPI_BIN="$(command -v mpirun)" || die '--skip-apt requires an installed mpirun'; fi
# Validate inputs before apt, downloads or configuration changes.
python3 "$HERE/scripts/prepare_kit.py" --source "$KIT_SOURCE" --destination "$KIT_DEST" --run-case-base "$CASE_ROOT" --check-only
python3 "$HERE/scripts/install_service.py" --code-root "$HERE" --data-root "$DATA_ROOT" --kit "$KIT_DEST" --run-case-base "$CASE_ROOT" --engine "$ENGINE" --mpirun "$MPI_BIN" --check-only
bash "$HERE/scripts/install_runtime.sh" "${RUNTIME_ARGS[@]}"
export PATH="$DATA_ROOT/bin:$DATA_ROOT/runtime/node/bin:$DATA_ROOT/runtime/npm-tools/bin:$DATA_ROOT/runtime/venv/bin:$PATH"
export MDSIM_PYTHON="$DATA_ROOT/runtime/venv/bin/python"
export MDSIM_NODE_BIN="$DATA_ROOT/runtime/node/bin"
export MDSIM_DATA_ROOT="$DATA_ROOT" RUN_CASE_BASE="$CASE_ROOT" MDSIM_KIT_BASE="$KIT_DEST" AGENT_BASE="$KIT_DEST"
export MDSIM_REGISTER_SCRIPT="$HERE/scripts/register_job.py"
export AGENTS_DIR="$KIT_DEST/agents/runtime/v3.3"
export LMP="$DATA_ROOT/bin/lmp_mpi" MPIRUN="$MPI_BIN" MPI_VARS="$DATA_ROOT/runtime/openmpi-env.sh"
"$MDSIM_PYTHON" "$HERE/scripts/prepare_kit.py" --source "$KIT_SOURCE" --destination "$KIT_DEST" --run-case-base "$CASE_ROOT"
"$MDSIM_PYTHON" "$HERE/scripts/install_config.py" prepare --kit "$KIT_DEST" --data-root "$DATA_ROOT" --run-case-base "$CASE_ROOT"
bash "$HERE/scripts/setup.sh" "$KIT_DEST"
((!NON_INTERACTIVE)) || MODEL_ARGS+=(--non-interactive)
((!RECONFIGURE)) || MODEL_ARGS+=(--reconfigure)
"$MDSIM_PYTHON" "$HERE/scripts/install_config.py" configure-model --data-root "$DATA_ROOT" "${MODEL_ARGS[@]}"
"$MDSIM_PYTHON" "$HERE/scripts/install_service.py" --code-root "$HERE" --data-root "$DATA_ROOT" --kit "$KIT_DEST" --run-case-base "$CASE_ROOT" --engine "$ENGINE" --mpirun "$MPI_BIN"
"$MDSIM_PYTHON" "$HERE/scripts/install_check.py" --data-root "$DATA_ROOT"
if ((!NO_START)); then
  command -v systemctl >/dev/null || die 'systemctl unavailable; rerun with --no-start and use the generated launcher'
  systemctl --user show-environment >/dev/null || die 'No user systemd bus; rerun with --no-start, then use the generated launcher'
  systemctl --user daemon-reload
  systemctl --user enable --now mdagent-web.service mdagent-dashboard.service mdagent-orchestrator.service mdagent-evolution.timer
  # Poll HTTP/UI and active service state; never send a prompt or submit a job.
  READY=0
  for ((attempt=0; attempt<30; attempt++)); do
    if "$MDSIM_PYTHON" "$HERE/scripts/install_check.py" --data-root "$DATA_ROOT" --services --quiet; then READY=1; break; fi
    sleep 1
  done
  ((READY)) || die 'Services failed readiness checks. Inspect journalctl --user -u mdagent-web -u mdagent-dashboard -u mdagent-orchestrator; installation is incomplete'
fi
printf '\n[install] Files and runtime checks passed.\n'
if ((NO_START)); then
  printf 'Services not started. Launcher: %s/bin/mdagent-local {web|dashboard|orchestrator|tui|check}\n' "$DATA_ROOT"
else
  printf 'Local workbench: http://127.0.0.1:8080/workbench\nChat: http://127.0.0.1:3091/\n'
fi
printf '%s\n' 'These checks do not certify a model answer, simulation result, or SSH cluster submission.'
