# `opencode` runtime — shared mechanics for both delegate roles

Family-level operational knowledge for the OpenCode CLI, shared by
`opencode-adversarial-review` (read-only via a strict permission config) and
`opencode-implement` (write, no-push machine-enforced). Every item here was
measured live (dates kept as provenance; the pool changes fast — re-verify).

## Why this family exists in the pool

The free pool is an **additional model family** at zero quota cost. Its named
models (DeepSeek, NVIDIA Nemotron, and others that come and go) are none of
GPT/Gemini/Claude — so a second reviewer on HIGH-risk work no longer has to
spend paid quota.

- **Stealth models (family deliberately undisclosed) are frontier-grade but
  can never SATISFY the cross-family rule** — they might be a GPT/Gemini/
  Claude variant under the hood. Fine as an additional reviewer or as an
  implementer; never the accounting leg, and never review a stealth model's
  own work with itself.
- **Re-confirm the catalogue with `opencode models` rather than trusting any
  list.** Measured: models appear and disappear; a model you never probe is
  a retry target you do not have when the ones you know are down.
- Vendor-keyed models (e.g. `google/*`) also appear in the catalogue — they
  bill the user's own API key. They are NOT the free pool; do not spend them
  without asking.
  - **Carve-out — OpenRouter's own `:free`-tagged models** (`openrouter/*:free`,
    e.g. `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`,
    `openrouter/z-ai/glm-5.2:free`): zero monetary cost like the native pool,
    but DO need the OpenRouter API key configured in opencode's auth first —
    so treat them as free-to-spend once that key exists, not as a
    no-credential-at-all model. Confirmed live 2026-08-22: 19 models across
    vendors the native pool doesn't otherwise reach (NVIDIA Nemotron variants,
    Z-AI GLM, Google Gemma, Cohere, Liquid, Poolside, ThinkingMachines,
    Dots-Studio). **Enumerate live, don't trust this count** —
    `opencode models | grep ':free$'`; OpenRouter's free tier churns
    independently of opencode's own pool and carries its own per-key rate
    limit (commonly tighter than the native pool's congestion behavior) —
    measure before routing a time-sensitive round through it.

## Auth: the free pool needs no credential

Measured: free-pool calls succeed with no login dance, no token expiry, no
silent-auth coin flip. (OpenRouter's `:free` tier is the one exception —
see the carve-out above; it needs the key but not spend.)

## Headless invocation — the prompt goes on STDIN, never in argv

```bash
cd "$TARGET" && opencode run --print-logs --log-level INFO \
  -m opencode/<model> \
  < "$RUN_DIR/prompt.md" > "$RUN_DIR/out.log" 2>&1
```

**This is not a style preference — argv cannot carry a real prompt.**
Measured across models: an argv prompt beyond ~1–2 KB hangs before the
session is even created — `message=init` and then nothing, forever. It is
SIZE, not content (filler text hangs identically). A size sweep pinned the
reliable-failure threshold at ≥2 KB. A second, fully deterministic argv bug:
**a prompt whose first character is `-` hangs forever** — the CLI parses it
as a flag and blocks on stdin that never arrives.

**Diagnosing a stalled run** — the bootstrap sequence is fixed, so where the
log stops names the failure:

| Last line reached | Meaning |
|---|---|
| `message=init` and nothing more | argv prompt too large — NOT congestion, NOT the model |
| `message=created id=…` → `event connected` → `stream providerID=…` | bootstrap fine; a stall after this is real upstream congestion |

The bootstrap lines appear within ~0.2 s on a healthy run. Their absence
looks identical across every model — three different backends cannot be
congested in exactly the same way at the same moment, so uniform silence is
a LOCAL fault. Confirm with a one-word probe: a trivial prompt answers in
under 10 s even while argv-launched runs beside it are wedged.

- Useful flags (verified via `--help`; re-verify per version):
  `-m provider/model`, `--agent <name>`, `--dir <path>`, `--format json`,
  `--variant`, and `--auto` (**never use it** — auto-approves anything not
  explicitly denied; this family's equivalent of a skip-permissions flag).
- **Always pass `--print-logs --log-level INFO`** and capture to a file: the
  INFO stream carries `projectID=` (config binding, below) and
  `evaluated permission=` (the per-tool-call audit trail).
- Launch under the host's background mechanism with a generous timeout.
- Every run fires a small title-generation call (`agent=title` in logs) —
  expected.

## The permission model — read this twice, both traps are silent

Rules live in `opencode.json` at the **project root** and merge into the
agent's rule array. Two measured traps:

1. **A repo with zero commits is not a project.** The log says
   `projectID=global` and the project's `opencode.json` is **silently
   ignored** — every rule you wrote simply doesn't exist. After one commit,
   `projectID` becomes a real hash and the config loads. Check the
   `projectID=` line on the first run in any fresh worktree.
2. **LAST match wins, so order is everything.** Measured in both directions:
   with `{"git push*": "deny", "*": "allow"}` the wildcard swallowed the deny
   and the denied command RAN. Write the wildcard FIRST, then the specific
   rules. The same ordering means **project config overrides agent
   defaults** — a project-level `"edit": "allow"` silently erased the plan
   agent's built-in edit-deny. One role, one directory, one config.

What a denial looks like: the tool call fails with a "rule prevents this
tool call" message, **the model receives it and keeps going** — it can
report MACHINE-DENIED and finish. With `--print-logs`, every tool call logs
`evaluated permission=… action.action=allow|deny` — the audit trail proving
which rule governed.

Do not rely on `--agent plan` as a review boundary: its edit-deny is
config-overridable and its bash rules are `*: allow` (measured). The
explicit deny-by-default config in the review skill is stronger.

## Congestion: the price of free

Measured at peak: `[503] The request queue is full` (the pool's own
gateway), `[502] Upstream error … ResourceExhausted`. Off-peak the same
models answered in seconds.

Congestion after bootstrap is SLOW rather than fatal: a measured review run
read all its files, went silent 10+ minutes inside the final generation,
resumed, and exited 0 with a full report at ~31 minutes. Budget 40m+ for a
real review; do not kill it at the first long silence — but only once the
bootstrap lines are confirmed (a pre-bootstrap stall is the argv bug, and no
amount of waiting fixes it).

Consequences:

- **A retry loop is mandatory**, cycling models, with the presence of an
  `evaluated permission=` line as the test that a run did real work.
- Errors are LOUD (nonzero exit + clear message); hangs are the quiet
  failure mode — bound every run with a timeout.
- Do not put the free pool on a time-critical path. It is the cheap second
  opinion and the cheap mechanical implementer, not the primary gate.
- **Switching models inside a bad window does not help** — the pool is one
  queue (re-measured three times). Wait, or route to a different family.

## n=1 proves nothing here — this pool is flaky BY DESIGN

A worked example of getting it wrong, kept because the mistake is the
default human move: chasing the argv hang, a single 4.4 KB argv run happened
to succeed, and a single tiny argv run in an MCP-wired repo happened to
hang. From two samples an entire causal story was built and stated
confidently ("argv size is not the cause; a remote MCP server blocks
startup") — complete with a synthetic reproduction that also hung once.
Repeating each leg destroyed it: tiny-argv passed 2/2 in the same MCP-wired
repo, stdin passed with MCP present, and a proper size sweep failed 4/4 at
2/5/9/17 KB. The MCP theory was noise; the documented argv finding was right
all along, and the sweep merely tightened its threshold.

Before writing any mechanism into this file: **run each arm at least twice,
and prefer a sweep to a pair of anecdotes.** A hang here has at least two
independent sources (argv size, post-bootstrap congestion) plus genuine
flakiness at the boundary; one observation cannot separate them.

## Measured reviewer calibration (keep your own)

This is where your calibration journal rows for this family live — one row
per run, dated, with verified hit rates (see
[`docs/calibration-journal.md`](../../../docs/calibration-journal.md) for the
format and rules). Highlights from the journal this file was extracted from,
kept as *shape examples*, not as your
data:

- A free leg returned 7 findings on a consistency diff, 7/7 verified real.
- The same model later found the only LIVE defect of a round on a wire
  contract — a silent-truncation bug that two paid legs and a human reviewer
  had all called the opposite way.
- The other named model: competent, lower yield on the same targets; its
  measured niche is huge-context work (native 1M context — a structural
  property, not a capability claim).
- In paired rounds against another family on one security design, this
  family was the LENIENT one on attack paths (passed a bearer-credential
  defeat, a pre-check that bounded nothing, a DoS-able saturation condition)
  while ALONE catching three document-consistency defects. Route briefs
  accordingly.
- Two consecutive zero-token completions in one window, both models, each
  having read every file — the pool is one queue.
- `tokens.output=0` was retracted as a verdict after a run with that exact
  fingerprint contained a complete, verified review — grep for the report's
  section headers before discarding a run.

## A long review eats its own prompt — put the ask in a FILE

Measured twice: a review that read many files came back having lost the
numbered claim list to context compaction. Reordering the prompt does not
help — compaction drops the original user message as a unit. The defense is
structural: put the claims in a file inside the review directory and tell
the model to re-read it on demand; give reading budgets for big files;
authorize a partial answer ("mark the rest NOT REACHED") so the model's
options aren't "guess" or "stop and ask".

## Instruction-layer inheritance

Measured: asked to `git push`, a free-pool delegate refused BEFORE the
machine layer saw it, quoting the user's global no-push instruction
verbatim. opencode natively reads `AGENTS.md` and can be granted read access
to a shared skills directory — so repo rules and the global instruction
layer travel with the delegate for free. Belt; the permission config is the
braces. State destructive-adjacent rules in the task prompt anyway.

## Housekeeping

- The per-role `opencode.json` you drop into a worktree shows up as an
  untracked file — expect it in scope checks, never stage it, remove at
  teardown.
- `opencode stats` reports token usage/cost per provider.
- `opencode debug config` (from the target dir) prints the resolved config;
  `opencode debug agent <name>` prints the merged rule array in evaluation
  order — the fastest way to verify what will govern before spending a run.
