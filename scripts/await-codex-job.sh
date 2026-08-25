#!/usr/bin/env bash
#
# Block until a Codex companion job reaches a terminal state, then print its
# status. Run this under the HOST'S OWN background mechanism: the host's
# "task finished" notification then fires when the JOB finishes, instead of
# when the launcher returned.
#
# WHY THIS EXISTS. `node codex-companion.mjs task --background ...` returns as
# soon as the job is accepted. If that launch is what you backgrounded, the
# host tells you it "completed" within seconds while the delegate has not
# started thinking — so nothing wakes you when the work is actually done.
# Measured 2026-08-25: three consecutive rounds finished 14, 20 and 40+ minutes
# before the lead noticed, each time only because the human asked.
#
# Usage:  await-codex-job.sh <job-id> [worktree] [max-seconds]
set -u

JOB=${1:?usage: await-codex-job.sh <job-id> [worktree] [max-seconds]}
WT=${2:-$PWD}
MAX=${3:-5400}

SCRIPT=$(ls -d "$HOME"/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs \
    2>/dev/null | sort -V | tail -1)
[ -n "$SCRIPT" ] || { echo "codex companion not installed" >&2; exit 2; }

# Job logs are the only reliable liveness signal; `status` computes "running"
# from startedAt and never checks the process (see codex-runtime.md).
find_log() {
    ls -t "$HOME/.claude/plugins/data/codex-openai-codex/state/"*/jobs/"$JOB.log" \
        2>/dev/null | head -1
}

deadline=$(( SECONDS + MAX ))
last_size=-1
quiet_since=0

while [ "$SECONDS" -lt "$deadline" ]; do
    line=$(cd "$WT" && timeout 60 node "$SCRIPT" status "$JOB" 2>/dev/null \
            | grep -F "$JOB" | head -1)

    case "$line" in
        *"| completed |"*|*"| failed |"*|*"| cancelled |"*|*"| error |"*)
            echo "TERMINAL: $line"
            exit 0
            ;;
    esac

    # Fallback for the launcher-output mode where no job is registered at all:
    # if a log exists and has not grown for 20 minutes, treat it as finished.
    # 20 minutes because the job writes nothing while parked inside a
    # collaboration/wait tool, and one such pause has lasted well past 10.
    log=$(find_log)
    if [ -n "$log" ] && [ -f "$log" ]; then
        size=$(stat -c %s "$log" 2>/dev/null || echo 0)
        if [ "$size" = "$last_size" ]; then
            [ "$quiet_since" -eq 0 ] && quiet_since=$SECONDS
            if [ $(( SECONDS - quiet_since )) -ge 1200 ]; then
                echo "QUIESCENT: $JOB log unchanged for 20m ($size bytes) — treating as done"
                exit 0
            fi
        else
            last_size=$size
            quiet_since=0
        fi
    fi

    sleep 20
done

echo "TIMEOUT: $JOB still not terminal after ${MAX}s" >&2
exit 1
