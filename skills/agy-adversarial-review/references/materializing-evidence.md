# Materializing evidence for an `agy` review leg

The cross-family rules — where the material goes, the four properties it must
carry, and the closing checks — are in
[`docs/materializing-evidence.md`](../../../docs/materializing-evidence.md).
**Read [`agy-runtime.md`](agy-runtime.md) first**; an example that contradicts
it is a bug however carefully the prose around it argues. Twice it drifted:
the allow-list prerequisite and the `if`-based exit-status capture were both
already documented there when this skill's example contradicted them.

This page carries only what is specific to `agy`.

## Why a brief cannot just say "run git"

A brief may only name commands on this machine's allow-list, **and a pipeline
is only as permitted as its least-permitted stage.**

The runtime file's one-time `permissions.allow` setup is a prerequisite, not a
suggestion: with it the delegate runs `git show/log/diff/status/branch/rev-parse`;
without it even read-only `git log` dies with zero output. Verify it before
blaming a leg.

What bites *after* that setup is the pipeline. Measured: a brief asking for
`git show … | sed -n '/^## A/,/^## B/p'` returned zero bytes on a machine whose
allow-list did contain `unsandboxed(git show)` — because it did not contain
`sed`. Headless has nowhere to prompt, so the call is auto-denied and the run
dies silently, indistinguishable from a dead leg. The denial names the missing
permission (`grep -i "permission check failed for unsandboxed"` in the log);
read it before concluding anything.

So keep briefs to allow-listed commands, and when the material needs a tool
that is not on the list — `sed`, `awk`, a formatter — materialize it yourself
instead of widening the allow-list for one run.

## Launching with the material granted

`$RUN_DIR` is granted with a **second `--add-dir`** (the flag is repeatable),
and `agy`'s status is captured through an `if` — a bare `cmd; rc=$?` lets a
caller's `set -e` exit before the capture, leaving `$RUN_DIR` unwritable:

```bash
if agy -p "$(cat "$RUN_DIR/prompt.md")" --model <gemini-tier> --mode plan --sandbox \
       --add-dir "$REVIEW_TARGET_DIR" --add-dir "$RUN_DIR" --effort high --print-timeout 15m0s
then status=0; else status=$?; fi
```

See the shared page for the surrounding guards (inventory, digest, `chmod`,
and returning `$status` after cleanup).
