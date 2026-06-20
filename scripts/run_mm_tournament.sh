#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-start}"
ROOT="${LIGHTER_MM_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"

default_run_id() {
  if [[ "$ACTION" != "start" && -z "${RUN_ID:-}" && -d "$ROOT/logs/tournament" ]]; then
    find "$ROOT/logs/tournament" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %f\n' 2>/dev/null \
      | sort -nr | awk 'NR==1 {print $2}'
    return
  fi
  date -u +%Y%m%dT%H%M%SZ
}

RUN_ID="${RUN_ID:-$(default_run_id)}"
if [[ -z "$RUN_ID" ]]; then
  RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
fi
LOG_ROOT="${TOURNAMENT_LOG_ROOT:-$ROOT/logs/tournament/$RUN_ID}"
GRID_CONFIG="${GRID_CONFIG:-$ROOT/configs/tournament_cj_100.json}"
SYMBOLS_FILE="${SYMBOLS_FILE:-$ROOT/configs/tournament_symbols_core.txt}"
DURATION_SEC="${DURATION_SEC:-$((4 * 24 * 3600))}"
CHECK_INTERVAL="${CHECK_INTERVAL:-3600}"
MAX_RESTARTS="${MAX_RESTARTS:-20}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/venv/bin/python}"
PID_FILE="$LOG_ROOT/tournament_pids.txt"
RUN_ENV="$LOG_ROOT/run.env"

read_symbols() {
  if [[ -n "${TOURNAMENT_SYMBOLS:-}" ]]; then
    printf "%s\n" $TOURNAMENT_SYMBOLS
    return
  fi
  grep -Ev '^\s*(#|$)' "$SYMBOLS_FILE"
}

require_python() {
  if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python not found at $PYTHON_BIN. Set PYTHON_BIN or create venv." >&2
    exit 1
  fi
}

start_tournament() {
  require_python
  mkdir -p "$LOG_ROOT"
  if [[ -f "$PID_FILE" ]]; then
    echo "PID file already exists: $PID_FILE" >&2
    echo "Use: RUN_ID=$RUN_ID $0 status|stop" >&2
    exit 1
  fi

  local expected_slots
  expected_slots=$("$PYTHON_BIN" - "$GRID_CONFIG" <<'PY'
import json
import sys
with open(sys.argv[1]) as f:
    cfg = json.load(f)
total = 1
for values in cfg.get("parameters", {}).values():
    total *= len(values)
print(total)
PY
)

  {
    echo "RUN_ID=$RUN_ID"
    echo "LOG_ROOT=$LOG_ROOT"
    echo "GRID_CONFIG=$GRID_CONFIG"
    echo "SYMBOLS_FILE=$SYMBOLS_FILE"
    echo "DURATION_SEC=$DURATION_SEC"
    echo "CHECK_INTERVAL=$CHECK_INTERVAL"
    echo "EXPECTED_SLOTS=$expected_slots"
    echo "STARTED_AT_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$RUN_ENV"

  echo "# symbol monitor_pid log_dir" > "$PID_FILE"
  while IFS= read -r symbol; do
    [[ -z "$symbol" ]] && continue
    local sym log_dir nohup_log
    sym="$(printf "%s" "$symbol" | tr '[:lower:]' '[:upper:]')"
    log_dir="$LOG_ROOT/$sym"
    nohup_log="$LOG_ROOT/${sym}_launcher.log"
    mkdir -p "$log_dir"
    (
      export MARKET_SYMBOL="$sym"
      export LOG_DIR="$log_dir"
      export GRID_CONFIG="$GRID_CONFIG"
      export DURATION_SEC="$DURATION_SEC"
      export CHECK_INTERVAL="$CHECK_INTERVAL"
      export MAX_RESTARTS="$MAX_RESTARTS"
      export PYTHON_BIN="$PYTHON_BIN"
      export ALPHA_SOURCE="${ALPHA_SOURCE:-lighter}"
      exec "$ROOT/run_grid_3d.sh"
    ) > "$nohup_log" 2>&1 &
    echo "$sym $! $log_dir" >> "$PID_FILE"
    echo "started $sym monitor_pid=$! log_dir=$log_dir"
    sleep 0.5
  done < <(read_symbols)

  echo "Tournament started: RUN_ID=$RUN_ID"
  echo "Logs: $LOG_ROOT"
}

stop_tournament() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "No PID file found: $PID_FILE" >&2
    exit 1
  fi
  while read -r symbol pid log_dir; do
    [[ "$symbol" == "#" || -z "${pid:-}" ]] && continue
    if kill -0 "$pid" 2>/dev/null; then
      echo "stopping $symbol monitor_pid=$pid"
      kill -TERM "$pid" 2>/dev/null || true
    else
      echo "$symbol monitor already stopped"
    fi
    if [[ -f "$log_dir/grid_monitor.pid" ]]; then
      local monitor_pid
      monitor_pid="$(cat "$log_dir/grid_monitor.pid" 2>/dev/null || true)"
      if [[ -n "$monitor_pid" ]] && kill -0 "$monitor_pid" 2>/dev/null; then
        kill -TERM "$monitor_pid" 2>/dev/null || true
      fi
    fi
  done < "$PID_FILE"
}

status_tournament() {
  if [[ -f "$RUN_ENV" ]]; then
    cat "$RUN_ENV"
  else
    echo "No run env found at $RUN_ENV"
  fi
  echo
  if [[ ! -f "$PID_FILE" ]]; then
    echo "No PID file found: $PID_FILE" >&2
    exit 1
  fi
  printf "%-8s %-10s %-8s %-8s %-12s %s\n" "SYMBOL" "PID" "MONITOR" "BOT" "STATE_FILES" "LAST_SUMMARY"
  while read -r symbol pid log_dir; do
    [[ "$symbol" == "#" || -z "${pid:-}" ]] && continue
    local monitor_state bot_state state_count last_summary bot_pid
    monitor_state="dead"
    kill -0 "$pid" 2>/dev/null && monitor_state="alive"
    bot_state="unknown"
    bot_pid=""
    if [[ -f "$log_dir/grid_monitor.pid" ]]; then
      bot_pid="$(pgrep -P "$(cat "$log_dir/grid_monitor.pid" 2>/dev/null)" -f 'market_maker_v2.py' | head -1 || true)"
    fi
    if [[ -n "$bot_pid" ]] && kill -0 "$bot_pid" 2>/dev/null; then
      bot_state="alive"
    else
      bot_state="n/a"
    fi
    state_count="$(find "$log_dir/grid" -maxdepth 1 -name 'state_*.json' 2>/dev/null | wc -l | tr -d ' ')"
    last_summary="$(grep '^GRID SUMMARY' "$log_dir/grid/summary.log" 2>/dev/null | tail -1 || true)"
    printf "%-8s %-10s %-8s %-8s %-12s %s\n" "$symbol" "$pid" "$monitor_state" "$bot_state" "$state_count" "$last_summary"
  done < "$PID_FILE"
}

report_tournament() {
  "$PYTHON_BIN" "$ROOT/scripts/report_mm_tournament.py" "$LOG_ROOT" "${@:2}"
}

case "$ACTION" in
  start)
    start_tournament
    ;;
  stop)
    stop_tournament
    ;;
  status)
    status_tournament
    ;;
  report)
    report_tournament "$@"
    ;;
  *)
    echo "usage: RUN_ID=<id> $0 <start|stop|status|report>" >&2
    exit 2
    ;;
esac
