#!/usr/bin/env python3
"""Print the dev-lead legs running as this user, as JSON, for the dev-lead-panel plugin.

A leg is a headless CLI run in the shape dev-lead's launch.json gives it
(codex exec, agy -p, cursor-agent -p, opencode run, claude -p with a pinned
permission mode, grok --prompt-file). Only the adapter, role, model,
effort, age and working-directory name leave this script: a command line can
carry a prompt or a secret, so it is never printed. The model and effort are
shown only where argv keeps its boundaries (Linux /proc, macOS KERN_PROCARGS2,
Windows CommandLineToArgvW), and only when they
look like an identifier and the model names a family in dev-lead's
data/families.json; anything else prints as "?". With QUOTA_PANEL_LEGS_PS=1 on
macOS (the ps fallback) a leg shows its adapter and age alone.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

WRAPPERS = {"bash", "sh", "node", "python3", "python", "env"}
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,63}")
EFFORT = re.compile(r"[a-z]{1,10}")
ANCESTRY = 8
CANDIDATES = WRAPPERS | {"codex", "agy", "cursor-agent", "opencode", "claude", "grok"}
# The installer's layout: ~/.local/share/cursor-agent/versions/<version>/cursor-agent
CURSOR = re.compile(r"/cursor-agent/versions/[^/]+/cursor-agent$")


def model_patterns():
    """Model-id substrings dev-lead knows a family for, from its newest release."""
    roots = sorted(glob.glob(os.path.expanduser("~/.claude/plugins/cache/dev-lead/dev-lead/*")),
                   key=lambda p: [int(x) if x.isdigit() else 0 for x in os.path.basename(p).split(".")])
    pats = []
    for root in reversed(roots):
        try:
            with open(os.path.join(root, "data", "families.json")) as f:
                fams = json.load(f).get("families", {})
        except (OSError, ValueError):
            continue
        for fam in fams.values():
            if isinstance(fam, dict):
                pats += [m.lower() for m in fam.get("models", []) if isinstance(m, str)]
        break
    return pats


def shown_model(model, pats):
    if not IDENT.fullmatch(model) or not any(p in model.lower() for p in pats):
        return "?"
    return model


def shown_effort(effort):
    return effort if EFFORT.fullmatch(effort) else ("?" if effort else "")


def base(path):
    name = os.path.basename(path.replace("\\", "/")).lower()
    for ext in (".exe", ".cmd"):
        if name.endswith(ext):
            name = name[: -len(ext)]
    return name


def after(argv, *flags):
    """The value of the one flag in `flags`, "" if absent, "?" if it occurs twice.

    Where the command line is one string split on spaces (the macOS ps fallback)
    a prompt can carry a flag of its own; the launcher passes each flag once, so a
    second copy means some copy came from the prompt and neither can be trusted.
    """
    at = [i for i, a in enumerate(argv[:-1]) if a in flags]
    if not at:
        return ""
    return argv[at[0] + 1] if len(at) == 1 else "?"


def role_of(value, review):
    return "?" if value == "?" else ("review" if value == review else "implement")


def classify(argv):
    """(adapter, role, model, effort) for a leg's argv, else None."""
    head = argv[:2]
    a = argv
    # A shebang can stack two wrappers (`/usr/bin/env python3 ./agy ...`).
    for _ in range(2):
        if a and base(a[0]) in WRAPPERS and len(a) > 1:
            a = a[1:]
    if not a:
        return None
    name = base(a[0])
    if name == "cursor-agent" or any(CURSOR.search(x.replace("\\", "/")) for x in head):
        if "-p" not in a:
            return None
        return ("cursor", role_of(after(a, "--mode"), "ask"), after(a, "--model"), "")
    if name in ("codex", "codex.js") and "exec" in a:
        # Only the value of a -c option before "--"; a prompt argument is not configuration.
        end = a.index("--") if "--" in a else len(a)
        efforts = [a[i + 1].split("=", 1)[1] for i in range(end - 1)
                   if a[i] == "-c" and a[i + 1].startswith("model_reasoning_effort=")]
        effort = "" if not efforts else (efforts[0] if len(efforts) == 1 else "?")
        return ("codex", role_of(after(a, "-s", "--sandbox"), "read-only"),
                after(a, "-m", "--model"), effort)
    if name == "codex-companion.mjs" and "task" in a:
        return ("codex", "implement", after(a, "--model"), after(a, "--effort"))
    if name == "agy" and "-p" in a:
        return ("agy", role_of(after(a, "--mode"), "plan"), after(a, "--model"), "")
    if name == "opencode" and len(a) > 1 and a[1] == "run":
        model, _, effort = after(a, "-m", "--model").partition("#")
        return ("opencode", "", model, effort)
    # dev-lead's claude legs pin a permission mode and turn off MCP (review) or
    # hooks (implement); a plain `claude -p`, such as the Remember plugin's, does neither.
    if (name == "claude" and "-p" in a and after(a, "--permission-mode") in ("plan", "acceptEdits", "?")
            and ("--strict-mcp-config" in a or "--settings" in a)):
        return ("claude", role_of(after(a, "--permission-mode"), "plan"),
                after(a, "--model"), after(a, "--effort"))
    if name == "grok" and "--prompt-file" in a:
        return ("grok", role_of(after(a, "--sandbox"), "read-only"),
                after(a, "-m", "--model"), after(a, "--effort"))
    return None


def procs_linux():
    uid = os.getuid()
    hz = os.sysconf("SC_CLK_TCK")
    with open("/proc/uptime") as f:
        uptime = float(f.read().split()[0])
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat("/proc/" + pid).st_uid != uid:
                continue
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                argv = [x.decode("utf-8", "replace") for x in f.read().split(b"\0") if x]
            with open("/proc/%s/stat" % pid) as f:
                stat = f.read().rsplit(")", 1)[1].split()
            cwd = os.readlink("/proc/%s/cwd" % pid)
        except OSError:
            continue
        yield int(pid), int(stat[1]), int(uptime - int(stat[19]) / hz), argv, cwd


def etime_secs(s):
    days, _, rest = s.rpartition("-")
    nums = [int(x) for x in rest.split(":")]
    while len(nums) < 3:
        nums.insert(0, 0)
    return int(days or 0) * 86400 + nums[0] * 3600 + nums[1] * 60 + nums[2]


def procs_ps():
    # macOS: list the processes by fields that hold no newline (comm is the
    # executable), then read argv per candidate pid. A prompt with a newline can
    # then only lengthen its own process's argv, never start a row of its own.
    run = subprocess.run(["ps", "-U", str(os.getuid()), "-o", "pid=,ppid=,etime=,comm="],
                         capture_output=True, text=True, timeout=10)
    if run.returncode != 0:
        sys.exit("ps failed (exit %d)" % run.returncode)
    for line in run.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid, secs = int(parts[0]), int(parts[1]), etime_secs(parts[2])
        except ValueError:
            continue
        if base(parts[3]) not in CANDIDATES:
            continue
        one = subprocess.run(["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True, timeout=10)
        if one.returncode != 0:
            continue  # gone since the listing
        yield pid, ppid, secs, one.stdout.split(), ""


# macOS, through libproc and sysctl: the kernel keeps each argument whole
# (KERN_PROCARGS2 is NUL-separated like /proc/<pid>/cmdline), so values are as
# trustworthy as on Linux. Layouts from <sys/proc_info.h> and <sys/sysctl.h>.
PROC_PIDTBSDINFO, BSDINFO_SIZE = 3, 136
PROC_PIDVNODEPATHINFO, VNODEPATHINFO_SIZE, CDIR_PATH_AT = 9, 2352, 152
CTL_KERN, KERN_ARGMAX, KERN_PROCARGS2 = 1, 8, 49
COMM_PREFIXES = tuple(sorted(c.encode() for c in CANDIDATES)) + (b"python",)


def parse_bsdinfo(raw):
    """(pid, ppid, uid, start seconds, start microseconds, comm) from a struct proc_bsdinfo."""
    import struct
    pid, ppid, uid = struct.unpack_from("<III", raw, 12)
    sec, usec = struct.unpack_from("<QQ", raw, 120)
    comm = raw[48:64].split(b"\0", 1)[0]
    return pid, ppid, uid, sec, usec, comm


def parse_procargs2(raw):
    """argv from a KERN_PROCARGS2 buffer: int argc, exec path, NUL padding, argc strings."""
    import struct
    if len(raw) < 4:
        return []
    argc = struct.unpack_from("<i", raw, 0)[0]
    at = raw.find(b"\0", 4)  # end of the exec path
    if argc <= 0 or at < 0:
        return []
    while at < len(raw) and raw[at] == 0:
        at += 1
    # Every one of the argc strings must end in its own NUL; an unterminated or
    # missing one means the buffer is incomplete and nothing in it is trusted.
    parts = raw[at:].split(b"\0")
    if len(parts) <= argc:
        return []
    return [a.decode("utf-8", "replace") for a in parts[:argc]]


def procs_macos():
    import ctypes
    import ctypes.util
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    n = libproc.proc_listallpids(None, 0)
    pids = (ctypes.c_int * (n + 64))()
    n = libproc.proc_listallpids(pids, ctypes.sizeof(pids))
    if n <= 0:
        sys.exit("proc_listallpids failed")
    argmax = ctypes.c_int()
    size = ctypes.c_size_t(ctypes.sizeof(argmax))
    if libc.sysctl((ctypes.c_int * 2)(CTL_KERN, KERN_ARGMAX), 2, ctypes.byref(argmax), ctypes.byref(size), None, 0):
        sys.exit("sysctl KERN_ARGMAX failed")
    uid, now = os.getuid(), time.time()
    buf = ctypes.create_string_buffer(argmax.value)

    def bsdinfo(pid):
        buf = ctypes.create_string_buffer(BSDINFO_SIZE)
        if libproc.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, buf, BSDINFO_SIZE) != BSDINFO_SIZE:
            return None
        return parse_bsdinfo(buf.raw)

    for pid in pids[:n]:
        if pid <= 0:
            continue
        info = bsdinfo(pid)
        if not info or info[2] != uid:
            continue
        # Cost only: skip processes whose name no leg or wrapper has (python shows
        # as python3.12 or Python, so names are matched by prefix).
        if not info[5].lower().startswith(COMM_PREFIXES):
            # Still yielded, argv-less, so main() keeps its parent link for the dedupe.
            yield pid, info[1], int(now - info[3]), [], ""
            continue
        size = ctypes.c_size_t(argmax.value)
        if libc.sysctl((ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid), 3, buf, ctypes.byref(size), None, 0):
            continue
        argv = parse_procargs2(buf.raw[:size.value])
        cwd = ""
        vbuf = ctypes.create_string_buffer(VNODEPATHINFO_SIZE)
        if libproc.proc_pidinfo(pid, PROC_PIDVNODEPATHINFO, 0, vbuf, VNODEPATHINFO_SIZE) == VNODEPATHINFO_SIZE:
            cwd = vbuf.raw[CDIR_PATH_AT:CDIR_PATH_AT + 1024].split(b"\0", 1)[0].decode("utf-8", "replace")
        if bsdinfo(pid) != info:
            continue  # the pid was reused (start time) or exec'd (comm) while it was read
        yield pid, info[1], int(now - info[3]), argv, cwd


def procs_windows():
    # Only this user's processes: GetOwner on the candidates whose name a leg can have.
    # UTF-8 both ways: a non-ASCII command line in the console's code page
    # (cp950 here) otherwise breaks the decode.
    ps = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
          "$n = 'codex.exe','agy.exe','cursor-agent.exe','opencode.exe','claude.exe','grok.exe','node.exe','python.exe','py.exe'; "
          "@(Get-CimInstance Win32_Process | Where-Object { $n -contains $_.Name } | "
          "Where-Object { (Invoke-CimMethod -InputObject $_ -MethodName GetOwner).User -eq $env:USERNAME } | "
          "Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine) | ConvertTo-Json -Compress")
    # 45 s: WMI can take 40 s on a busy laptop; the pane gives the whole scan 50 s there.
    run = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    if run.returncode != 0:
        sys.exit("process list failed (exit %d)" % run.returncode)
    now = time.time()
    rows = json.loads(run.stdout or "[]")
    if isinstance(rows, dict):
        rows = [rows]
    for p in rows:
        line = p.get("CommandLine") or ""
        created = str(p.get("CreationDate") or "")
        # ConvertTo-Json writes dates as "/Date(<ms>)/".
        ms = int(created[6:-2]) if created.startswith("/Date(") and created[6:-2].isdigit() else 0
        argv = split_windows(line)
        yield p["ProcessId"], p["ParentProcessId"], int(now - ms / 1000) if ms else 0, argv, ""


def split_windows(line):
    """argv as the program itself parses its command line: shell32's
    CommandLineToArgvW, the rule the C runtime, Rust, Node and Python share.
    A launcher quotes each argument, so a prompt stays one element."""
    import ctypes
    from ctypes import wintypes
    shell32, kernel32 = ctypes.windll.shell32, ctypes.windll.kernel32
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    if not line:
        return []
    n = ctypes.c_int()
    arr = shell32.CommandLineToArgvW(line, ctypes.byref(n))
    if not arr:
        return []
    try:
        return [arr[i] for i in range(n.value)]
    finally:
        kernel32.LocalFree(arr)


def main():
    # /proc (Linux), KERN_PROCARGS2 (macOS) and CommandLineToArgvW (Windows) give
    # argv with its boundaries. The ps fallback has one string only, where a prompt
    # (with spaces or newlines) could forge a flag, so it shows adapter and age.
    if sys.platform == "win32":
        exact, procs = True, procs_windows()
    elif os.path.isdir("/proc/self"):
        exact, procs = True, procs_linux()
    elif sys.platform == "darwin" and os.environ.get("QUOTA_PANEL_LEGS_PS") != "1":
        exact, procs = True, procs_macos()
    else:
        exact, procs = False, procs_ps()
    parent = {}
    found = {}
    for pid, ppid, secs, argv, cwd in procs:
        parent[pid] = ppid
        leg = classify(argv)
        if leg:
            found[pid] = (secs, leg, cwd)
    pats = model_patterns()
    legs = []
    for pid, (secs, leg, cwd) in found.items():
        # A wrapper and the CLI it starts are one leg: keep the outermost of an adapter.
        up, nested = parent.get(pid), False
        for _ in range(ANCESTRY):
            if up is None or up <= 1:
                break
            if up in found and found[up][1][0] == leg[0]:
                nested = True
                break
            up = parent.get(up)
        if nested:
            continue
        adapter, role, model, effort = leg
        if not exact:
            role, model, effort = "", "", ""
        legs.append({"adapter": adapter, "role": role, "model": shown_model(model, pats) if model else "",
                     "effort": shown_effort(effort), "secs": max(0, secs),
                     "dir": os.path.basename(cwd.rstrip("/"))})
    legs.sort(key=lambda x: -x["secs"])
    json.dump({"legs": legs}, sys.stdout)


if __name__ == "__main__":
    main()
