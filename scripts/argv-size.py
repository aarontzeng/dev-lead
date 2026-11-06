#!/usr/bin/env python3
"""Refuse a brief that Windows could not pass as ONE command-line argument.

    argv-size.py <file> <extra-units>

leg-cmd.sh puts this in front of an argv-delivered paid command when it composes for Windows. A prompt passed as an argument
failed above about 32 KB with `Argument list too long`, wrote an empty report and still exited 0, which read as a clean leg
(Win-Lead, 2026-11-06). The limit is on the SERIALISED command line in UTF-16 code units (CreateProcess: 32767), not on the
file's bytes, so this measures what is actually sent:

  * UTF-16 units, not UTF-8 bytes: a Chinese brief is 3 bytes a character but 1 unit, and must not be refused for its bytes;
  * the argument quoting Windows applies (CommandLineToArgvW rules): the argument is wrapped in quotes, each `"` becomes `\\"`,
    and a run of backslashes before a `"` is doubled -- a quote-dense brief grows by up to 2x;
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
    size = serialised_units(text) + extra
    if size > LIMIT:
        print("leg-cmd: the brief would make a %d-unit Windows command line, over the %d limit (Windows passes the prompt as one "
              "command-line argument: UTF-16 units after quoting, plus the rest of the command, about 32 KB in all). Shorten "
              "the brief, or put the bulk in a file inside the target and tell the leg to read it; for a framed leg give the "
              "retry a FRESH --run-dir (the evidence directory already exists)." % (size, LIMIT), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
