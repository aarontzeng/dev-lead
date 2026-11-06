#!/usr/bin/env python3
"""Refuse a brief that Windows could not pass as ONE command-line argument.

    argv-size.py <file> <extra-units>

leg-cmd.sh puts this in front of an argv-delivered paid command when it composes for Windows. A prompt passed as an argument
failed above about 32 KB with `Argument list too long`, wrote an empty report and still exited 0, which read as a clean leg
(Win-Lead, 2026-11-06). Two limits apply on the Git Bash path and this takes the larger of the two measures:

  * Git Bash's own exec limit counts UTF-8 BYTES of the argument, the same for Chinese and ASCII -- measured on a real Windows
    (Win-Lead, 2026-11-07, one argument to python.exe): 32500 bytes ran, 32700 failed with `Argument list too long`; a
    10800-character Chinese brief (32400 bytes) ran, 10900 characters (32700 bytes) failed. UTF-16 units under-count a Chinese
    brief threefold, which is why a 12000-character brief passed the first version of this guard and then died in bash;
  * CreateProcess's 32767 UTF-16 units of the SERIALISED command line, with the argument quoting Windows applies
    (CommandLineToArgvW rules): the argument is wrapped in quotes, each `"` becomes `\\"`, and a run of backslashes before a `"`
    is doubled -- a quote-dense brief grows by up to 2x, which its bytes do not show;
  * `<extra-units>`: the rest of the command line, which leg-cmd.sh knows when it composes (the CLI, its flags, any
    --add-dir, the timeout wrapper) plus a margin for the paths its shell variables expand to.

Exit 0 when it fits; 1 when it does not (a message on stderr says the size, the limit and what to do); 2 when the file
cannot be read (fail closed -- never run the paid command on an unreadable brief).
"""
import sys

LIMIT = 32000        # CreateProcess takes 32767 units; the rest is margin for the bits this cannot see


def serialised_units(text):
    """UTF-16 units of `text` as one quoted Windows command-line argument."""
    units = len(text.encode("utf-16-le", "surrogatepass")) // 2
    extra = 2                                     # the surrounding quotes
    run = 0
    for ch in text:
        if ch == "\\":
            run += 1
            continue
        if ch == '"':
            extra += run + 1                      # the backslashes before a quote are doubled, the quote is escaped
        run = 0
    extra += run                                  # a trailing run is doubled too (it precedes the closing quote)
    return units + extra


def measured_size(text):
    """The larger of the two measures (see the module doc): UTF-8 bytes (Git Bash's exec limit) and serialised UTF-16 units."""
    return max(len(text.encode("utf-8", "surrogatepass")), serialised_units(text))


def main(argv):
    if len(argv) != 2:
        print("usage: argv-size.py <file> <extra-units>", file=sys.stderr)
        return 2
    path, extra_raw = argv
    try:
        extra = int(extra_raw)
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "replace")
    except (OSError, ValueError) as exc:
        print("leg-cmd: cannot measure the brief %s: %s" % (path, exc), file=sys.stderr)
        return 2
    size = measured_size(text) + extra
    if size > LIMIT:
        print("leg-cmd: the brief would make a %d-unit Windows command line, over the %d limit (Windows passes the prompt as one "
              "command-line argument: the larger of its UTF-8 bytes and its quoted UTF-16 units, plus the rest of the command, "
              "about 32 KB in all; a Chinese character costs 3). Shorten "
              "the brief, or put the bulk in a file inside the target and tell the leg to read it; for a framed leg give the "
              "retry a FRESH --run-dir (the evidence directory already exists)." % (size, LIMIT), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
