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
# How long a job log may sit unchanged before the fallback below calls the
# job done. 20 minutes because the job writes nothing while parked inside a
# collaboration/wait tool, and one such pause has lasted well past 10. The
# override exists for the test that exercises the fallback.
QUIET=${AWAIT_QUIET_SECS:-1200}
POLL=${AWAIT_POLL_SECS:-20}

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
    # No GNU `timeout` here: macOS ships without it, and its absence made this
    # poll exit 127 on every iteration — the terminal case below never fired and
    # a 3.5-minute preflight refusal was discovered 10 minutes later, by the
    # human (2026-08-30). perl's alarm is on every macOS and Linux base install.
    line=$(cd "$WT" && perl -e 'alarm 60; exec @ARGV' node "$SCRIPT" status "$JOB" 2>/dev/null \
            | grep -F "$JOB" | head -1)

    case "$line" in
        *"| completed |"*|*"| failed |"*|*"| cancelled |"*|*"| error |"*)
            echo "TERMINAL: $line"
            exit 0
            ;;
    esac

    # Fallback for the launcher-output mode where no job is registered at all:
    # if a log exists and has not grown for $QUIET seconds, treat it as finished.
    log=$(find_log)
    if [ -n "$log" ] && [ -f "$log" ]; then
        # `wc -c` is the one byte count spelled the same on macOS and Linux. The
        # GNU-only `stat -c` returned 0 forever on macOS (a flat timer); the
        # BSD-first `stat -f %z || stat -c %s` that replaced it was worse on
        # Linux, where `stat -f` is FILESYSTEM status and SUCCEEDS -- so `size`
        # held a block of free-space figures that changed with every write to
        # the disk, and the fallback could not fire at all (found 2026-09-25).
        size=$(wc -c < "$log" 2>/dev/null | tr -d ' ' || echo 0)
        if [ "$size" = "$last_size" ]; then
            [ "$quiet_since" -eq 0 ] && quiet_since=$SECONDS
            if [ $(( SECONDS - quiet_since )) -ge "$QUIET" ]; then
                echo "QUIESCENT: $JOB log unchanged for ${QUIET}s ($size bytes) — treating as done"
                exit 0
            fi
        else
            last_size=$size
            quiet_since=0
        fi
    fi

    sleep "$POLL"
done

echo "TIMEOUT: $JOB still not terminal after ${MAX}s" >&2
exit 1
