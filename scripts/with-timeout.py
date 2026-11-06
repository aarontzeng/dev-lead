#!/usr/bin/env python3
"""Run a command with a wall-clock limit, and say plainly when the limit ended it.

    with-timeout.py <minutes> [--label TEXT] [--source TEXT] -- <command> [args...]

leg-cmd.sh wraps every paid leg in this. It exists instead of coreutils `timeout`
for three reasons: macOS has no `timeout`; the whole process tree has to die (a
CLI such as cursor-agent forks node workers that outlive a plain kill); and the
bare `timeout` result -- exit 124 and nothing else -- is exactly what read as a
HUNG leg on 2026-11-06. A grok-4.7-high review of a 12K-line file was working
(its stream showed thirteen tool calls in four minutes) when a hand-written
20-minute `timeout` killed it; `--output-format json` prints nothing until the
end, so the empty log and rc=124 looked like a hang and cost the lead three
retries on three other models.

Exit status: the command's own, or 124 when the limit ended it (GNU timeout's
spelling, so callers that already test for 124 keep working), or 128+N when a
signal N aimed at this wrapper ended it. Standard streams are inherited, never
captured: a leg's output streams through as it is written.

The tree: the child runs in its own session, so one killpg reaches everything that
stayed in the group; descendants that left it (setsid, node `detached: true`) are
found by walking the parent links BEFORE anything is signalled, and killed by pid.
On Windows `taskkill /T /F` walks the tree. A signal aimed at this wrapper is only
RECORDED by the handler; the kill, the message and the exit happen in the main flow
(a handler that waited on the child could deadlock on subprocess's own lock).
"""
import os
import signal
import subprocess
import sys
import time
from decimal import Decimal, InvalidOperation

TIMED_OUT = 124
GRACE = 10.0          # seconds between the polite signal and the final one; WITH_TIMEOUT_GRACE shortens it for tests
MAX_MINUTES = 7 * 24 * 60     # beyond a week a "cap" is no cap: say 0 (leg-cmd.sh --timeout 0) when none is meant


def parse_minutes(raw):
    """A finite, representable number of minutes, 0 or more, at most a week -- or ValueError saying what is wrong.

    float() alone lets through the shapes that silently turn a cap into no cap: '1e-1000' underflows to 0.0, '-1e-999'
    to negative zero, and 1e308 * 60 overflows to inf, so the limit would never fire while stderr still announced one.
    This one validator serves --timeout, the wrapper AND the numbers in data/launch.json (leg-cmd.sh, lint.py)."""
    s = str(raw).strip()
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError("minutes must be a number, got %r" % raw) from None
    if not d.is_finite():
        raise ValueError("minutes must be finite, got %r" % raw)
    if d < 0 or (d == 0 and s.lstrip().startswith("-")):
        raise ValueError("minutes must not be negative, got %r" % raw)
    v = float(d)
    if d != 0 and v == 0.0:
        raise ValueError("minutes is too small to represent (it would read as 0 = no cap), got %r" % raw)
    if v > MAX_MINUTES:
        raise ValueError("minutes is more than a week (%d), which is no cap at all; use 0 for none, got %r" % (MAX_MINUTES, raw))
    return v


def grace_from_env(raw, default=10.0):
    """WITH_TIMEOUT_GRACE is inherited from the lead's environment: anything but a finite 0..300 keeps the default."""
    try:
        v = float(Decimal(str(raw).strip()))
    except (InvalidOperation, ValueError):
        return default
    return v if 0 <= v <= 300 else default


def _usage(msg=""):
    if msg:
        print("with-timeout: %s" % msg, file=sys.stderr)
    print("usage: with-timeout.py <minutes> [--label TEXT] [--source TEXT] -- <command> [args...]", file=sys.stderr)
    return 2


def _fmt(minutes):
    return ("%d" % minutes) if float(minutes).is_integer() else ("%g" % minutes)


LIST_TIMEOUT = 10.0   # seconds one `ps` listing may take; WITH_TIMEOUT_LIST_TIMEOUT shortens it for tests


def _list_timeout():
    """WITH_TIMEOUT_LIST_TIMEOUT, finite and small, else the default (it is inherited from the lead's environment)."""
    raw = os.environ.get("WITH_TIMEOUT_LIST_TIMEOUT")
    if raw is None:
        return LIST_TIMEOUT
    v = grace_from_env(raw, LIST_TIMEOUT)
    return v if v > 0 else LIST_TIMEOUT


def _ps(argv):
    """Run a listing helper; a stalled one is killed and reaped, never left behind, and nothing here waits without a bound.
    COLUMNS is forced wide: ps truncates a line at the terminal width and lstart is the LAST field. Returns (stdout, pid)."""
    try:
        helper = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=dict(os.environ, COLUMNS="1000", LC_ALL="C"))
    except OSError:
        return None
    try:
        stdout, stderr = helper.communicate(timeout=_list_timeout())
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
        helper.kill()
        try:
            helper.communicate(timeout=2)
        except (subprocess.SubprocessError, OSError):
            pass                                   # a grandchild holding the pipe: let go of it rather than wait on it
        for stream in (helper.stdout, helper.stderr):
            try:
                stream.close()
            except (OSError, AttributeError):
                pass
        try:
            helper.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        return None
    return (stdout, helper.pid, helper.returncode, stderr)


def _snapshot():
    """{pid: (ppid, state, start, pgid)} for every process, or None when it cannot be read.

    Linux reads /proc (no tool needed, start time in clock ticks); elsewhere `ps` (macOS/BSD/busybox spellings tried in
    turn). The start time is the process's identity: a pid that comes back with another start time is a different process.
    WITH_TIMEOUT_SNAPSHOT=ps forces the ps path (a test seam)."""
    if os.path.isdir("/proc/self") and os.environ.get("WITH_TIMEOUT_SNAPSHOT") != "ps":
        out = {}
        try:
            names = os.listdir("/proc")
        except OSError:
            return None
        for name in names:
            if not name.isdigit():
                continue
            got = _proc_stat(int(name), strict=True)
            if got is _UNKNOWN:
                return None                            # a process we could not read is a hole in the listing: not a listing
            if got:
                out[int(name)] = got
        return out or None
    for argv in (["ps", "-A", "-ww", "-o", "pid=,ppid=,pgid=,stat=,lstart="], ["ps", "-ax", "-ww", "-o", "pid=,ppid=,pgid=,stat=,lstart="]):
        res = _ps(argv)
        if res is None:
            continue
        stdout, helper_pid, status, _err = res
        if status != 0:
            continue
        out = {}
        for line in stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
                out[int(parts[0])] = (int(parts[1]), parts[3], " ".join(parts[4].split()), int(parts[2]))
        out.pop(helper_pid, None)                  # the listing process is our own child, not the command's
        if out:
            return out
    return None


_UNKNOWN = object()   # a probe that could not tell: NOT the same as "that process is gone"


def _proc_stat(pid, strict=False):
    """(ppid, state, start, pgid) from /proc/<pid>/stat; None if the process is gone. With strict, a read that fails for any
    other reason (permission, a malformed line) is _UNKNOWN instead of None -- a failed look is not proof of absence."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as f:
            raw = f.read().decode("utf-8", "replace")
        rest = raw[raw.rindex(")") + 2:].split()      # after the "(comm)" field, which may hold spaces
        return (int(rest[1]), rest[0], rest[19], int(rest[2]))
    except (FileNotFoundError, ProcessLookupError):
        return None
    except (OSError, ValueError, IndexError):
        return _UNKNOWN if strict else None


def _probe(pid):
    """The CURRENT (ppid, state, start, pgid) of one pid, read now and on its own -- never from an older listing.
    None means the process is confirmed absent; _UNKNOWN means the look itself failed (ps would not run, timed out, or
    answered something unreadable) and says nothing about whether the process is alive."""
    if os.path.isdir("/proc/self") and os.environ.get("WITH_TIMEOUT_SNAPSHOT") != "ps":
        return _proc_stat(pid, strict=True)
    res = _ps(["ps", "-ww", "-o", "ppid=,pgid=,stat=,lstart=", "-p", str(pid)])
    if res is None:
        return _UNKNOWN
    stdout, _helper, status, err = res
    parts = stdout.split(None, 3)
    if status == 0 and len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit():
        return (int(parts[0]), parts[2], " ".join(parts[3].split()), int(parts[1]))
    if status == 1 and not stdout.strip() and not err.strip():
        return None                                # ps ran, said nothing, and found no such process

    return _UNKNOWN


def _walk(snap, root):
    """{pid: (start, pgid)} of every descendant of root in the snapshot (zombies are not processes anyone must stop)."""
    kids = {}
    for pid, entry in snap.items():
        kids.setdefault(entry[0], []).append(pid)
    found, todo = {}, [root]
    while todo:
        for c in kids.get(todo.pop(), []):
            if c not in found:
                found[c] = (snap[c][2], snap[c][3])
                todo.append(c)
    return found


def _live(snap, pid, start):
    """True while pid is still the SAME, not-yet-dead process in this listing: same start time, and not a zombie."""
    entry = snap.get(pid)
    return bool(entry) and entry[2] == start and not entry[1].startswith("Z")


def _signal_verified(pid, start, sig):
    """Signal pid only if it is, right now, the process recorded under `start`. Returns "sent", "gone" (it is no longer that
    process: nothing to stop) or "failed" (it IS that process and could not be signalled safely -- the caller must say so).

    Linux with pidfd: the fd is opened, the identity is read AGAIN, and the signal goes through the fd, so a number recycled
    in between cannot be hit. If pidfd exists but cannot be opened (fd exhaustion, a seccomp filter) the pid is NOT signalled:
    falling back to a bare os.kill would reopen the very race the fd closes. Without pidfd (macOS/BSD, old kernels) the
    window is the gap between the re-read and the kill."""
    cur = _probe(pid)
    if cur is _UNKNOWN:
        return "failed"
    if cur is None or cur[2] != start or cur[1].startswith("Z"):
        return "gone"
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            return "failed"
        try:
            again = _probe(pid)
            if again is _UNKNOWN:
                return "failed"
            if again is None or again[2] != start:
                return "gone"
            signal.pidfd_send_signal(fd, sig)
            return "sent"
        except OSError:
            return "failed"
        finally:
            os.close(fd)
    try:
        os.kill(pid, sig)
        return "sent"
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "failed"


def _send(proc, sig, targets, skip_group=False, deadline=None):
    """Signal the group only while the leader is still unreaped (its pid is then still ours), and each recorded pid only
    if it is still the process we recorded, checked at the moment of sending (not against an older listing, which may be
    all we have when a refresh fails). Once the leader is reaped its number may name an unrelated process or group, so
    nothing is sent to it. skip_group: members of the leader's group already got the group signal. `deadline` (monotonic)
    bounds the whole pass -- every verification can cost a ps call. Returns the pids NOT stopped: those not reached in time
    and those verified as the recorded process but impossible to signal safely."""
    leader_alive = proc.poll() is None
    if leader_alive:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
    skipped = []
    for p, (st, pg) in targets.items():
        if skip_group and leader_alive and pg == proc.pid:
            continue
        if deadline is not None and time.monotonic() > deadline:
            skipped.append(p)
            continue
        if _signal_verified(p, st, sig) == "failed":
            skipped.append(p)
    return skipped


def _reap_adopted(leader_pid):
    """With the subreaper set, orphans of the command become this wrapper's children and, once they exit, zombies. Reap them
    as they appear -- but never the leader (Popen owns its status): peek with WNOWAIT, then wait for that pid alone. Any
    error here (a syscall filter, no waitid) turns reaping off: it is housekeeping and must never break the stop."""
    global _REAP
    if not _SUBREAPER or not _REAP or not hasattr(os, "waitid"):
        return
    for _ in range(64):
        try:
            info = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return
        except OSError:
            _REAP = False
            return
        if info is None or info.si_pid == leader_pid:
            return
        try:
            os.waitpid(info.si_pid, 0)
        except ChildProcessError:
            pass
        except OSError:
            _REAP = False
            return


_SUBREAPER = False   # the kernel adopts this process's orphans (walks start from the wrapper)
_REAP = True         # reaping them is available; a denied waitid turns it off, which does not change who adopts them


def _become_subreaper():
    """Linux: orphans of the command are re-parented to this wrapper instead of init, so a child it starts while shutting
    down (a SIGTERM handler that forks and exits) can still be found by walking from here."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes
        return ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0     # PR_SET_CHILD_SUBREAPER
    except Exception:
        return False


def stop_tree(proc, first=signal.SIGTERM if os.name != "nt" else None, grace=None):
    """End the child and everything it started: the polite signal, a grace period, then the final one. Never blocks forever."""
    grace = GRACE if grace is None else grace
    if os.name == "nt":
        failed = False
        try:
            failed = subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15).returncode != 0
        except (OSError, subprocess.SubprocessError):
            failed = True                         # no taskkill, or it hung: fall back to the leader alone
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.kill()
        return ["taskkill could not stop the whole tree; children of the command may still be running"] if failed else []
    def expand(snapshot, found):
        """Add what is below the roots in this listing. A root is the wrapper itself (subreaper), or the leader while it is
        still unreaped -- never a reaped leader's number, which may name an unrelated process now -- plus every recorded
        process that is verifiably the same one in this very listing."""
        roots = [os.getpid()] if _SUBREAPER else ([proc.pid] if proc.poll() is None else [])
        roots += [p for p, (st, _pg) in found.items() if _live(snapshot, p, st)]
        for r in roots:
            found.update(_walk(snapshot, r))
        found.pop(os.getpid(), None)

    snap = _snapshot()                            # BEFORE signalling: once a parent dies its children are reparented
    if snap is None:
        print("with-timeout: cannot list processes (/proc and ps both failed); only the command's own process group is "
              "stopped, a descendant that left it may survive", file=sys.stderr)
    targets = {}
    if snap is not None:
        expand(snap, targets)
    last, warned = snap, snap is None
    unreached = _send(proc, first, targets, skip_group=True, deadline=time.monotonic() + 10)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        _reap_adopted(proc.pid)
        cur = _snapshot()
        if cur is None:
            if not warned:
                print("with-timeout: cannot refresh the process list; finishing with the last one", file=sys.stderr)
                warned = True
            time.sleep(0.1)                       # liveness is unknowable: do not call the stop complete early
            continue
        last = cur
        expand(cur, targets)                      # anything started while the command was shutting down
        if proc.poll() is not None and not any(_live(cur, p, st) for p, (st, _pg) in targets.items()):
            break
        time.sleep(0.1)
    cur = _snapshot() or last
    if cur is not None:
        expand(cur, targets)
    unreached += _send(proc, signal.SIGKILL, targets, deadline=time.monotonic() + 30)   # each pid is re-verified as it is signalled
    # a signal that was sent is not a process that is gone (uninterruptible I/O keeps it, SIGKILL pending): look again, briefly
    # "stopped" is claimed only when a final listing CONFIRMS it: a failed listing is not proof that nothing is left
    survivors, confirmed = [], not targets
    for _ in range(25):
        if confirmed:
            break
        now = _snapshot()
        if now is None:
            time.sleep(0.2)
            continue
        survivors = [p for p, (st, _pg) in targets.items() if _live(now, p, st)]
        if not survivors:
            confirmed = True
            break
        time.sleep(0.2)
    unreached += survivors
    if not confirmed and not survivors:
        unreached.append("(the final process listing failed, so the stop could not be confirmed)")
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        print("with-timeout: the process did not end after SIGKILL (pid %d); it may still be running" % proc.pid,
              file=sys.stderr)
    _reap_adopted(proc.pid)
    return sorted(set(unreached), key=str)


def unstopped_note(pids):
    """What to tell the lead when the stop was not complete: the leg may still be running and billing."""
    if not pids:
        return ""
    return ("  NOT everything was stopped: these could not be verified and signalled safely, or the stop could not be\n"
            "  confirmed (pidfd unavailable, a denied syscall, out of time, an unreadable process list) and may still be running:\n"
            "  %s\n" % ", ".join(str(p) for p in pids))


def main(argv):
    global GRACE
    GRACE = grace_from_env(os.environ.get("WITH_TIMEOUT_GRACE", GRACE), GRACE)
    args = list(argv)
    if "--" not in args:
        return _usage("missing `--` before the command")
    cut = args.index("--")
    head, command = args[:cut], args[cut + 1:]
    if not command:
        return _usage("no command after `--`")
    label = source = ""
    rest = []
    it = iter(head)
    for tok in it:
        if tok in ("--label", "--source"):
            val = next(it, None)
            if val is None:
                return _usage("%s needs a value" % tok)
            if tok == "--label":
                label = val
            else:
                source = val
        else:
            rest.append(tok)
    if len(rest) != 1:
        return _usage("expected exactly one <minutes> before `--`")
    try:
        minutes = parse_minutes(rest[0])
    except ValueError as exc:
        return _usage(str(exc))
    if not minutes > 0:
        return _usage("minutes must be greater than 0 (leg-cmd.sh emits no wrapper for --timeout 0)")

    # Handlers first: a signal that lands between the spawn and a later install would end the wrapper by default and leave
    # the child, which sits in its own session, running. The handler only RECORDS; the main flow does the stopping.
    global _SUBREAPER
    got = []
    if os.name != "nt":
        # SIGTSTP (^Z) is a stop too: the command sits in its own session, so ^Z would otherwise freeze only this wrapper
        # while the paid command keeps running, with the deadline no longer ticking
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGTSTP):
            signal.signal(s, lambda signum, _frame: got.append(signum))
        _SUBREAPER = _become_subreaper()
    kwargs = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True      # its own process group, so one killpg reaches everything that stays in it
    try:
        proc = subprocess.Popen(command, **kwargs)
    except OSError as exc:
        print("with-timeout: cannot start %s: %s" % (command[0], exc), file=sys.stderr)
        return 127 if isinstance(exc, FileNotFoundError) else 126

    deadline = time.monotonic() + minutes * 60
    rc = None
    try:
        while rc is None:
            if got:
                break
            _reap_adopted(proc.pid)
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                rc = proc.wait(timeout=min(left, 0.5))
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:                     # Windows (and a ^C that beat the handler): the same stop
        got.append(signal.SIGINT)
    except BaseException:
        stop_tree(proc)                           # an unexpected exception: never leave the paid command running
        raise
    if got:
        # a signal was aimed at us: stop what is left (the leader may even have just exited) and say it was the signal
        # ^Z means "stop" to a terminal, but a frozen tree would just sit in the grace period: end it with SIGTERM instead
        first = signal.SIGTERM if got[0] == getattr(signal, "SIGTSTP", None) else got[0]
        left_running = stop_tree(proc, first=first if os.name != "nt" else None)
        if left_running:
            print("with-timeout: stopped on signal %d.\n%s" % (got[0], unstopped_note(left_running)), file=sys.stderr)
        return 128 + got[0]
    if rc is None:
        left_running = stop_tree(proc)
        print("with-timeout: %s TIMED OUT after %s min and was stopped.\n"
              "  This is the wrapper's limit, NOT a verdict that the leg was hung: a leg that prints only at the end\n"
              "  (cursor-agent --output-format json) shows an empty log until it finishes, so a long healthy run ends\n"
              "  exactly like this. %sIf the leg was still working, rerun with a larger --timeout <minutes> (leg-cmd.sh)."
              % (label or command[0], _fmt(minutes), ("Limit from: %s.\n  " % source) if source else "")
              + unstopped_note(left_running), file=sys.stderr)
        return TIMED_OUT
    return rc if rc >= 0 else 128 - rc          # killed by signal N -> 128+N, the shell's spelling


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
