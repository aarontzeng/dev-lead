# Provisioning a delegate's worktree

Read this from `dev-lead` Phase 1, before the first dispatch of a session
into a repo whose tests need anything git does not track. Three measured
failures, each with the rule it paid for. The skill keeps the rules; this
file keeps the evidence.

**The worktree is missing everything the suite needs that git does not track,
and the delegate will not tell you — it will quietly use something else.**
Worktree isolation is usually discussed as "what is shared" (databases, ports,
the `.git` index). The failure that actually bites is the opposite category:
the virtualenv, the `.env`, the built assets, the fixture cache. They are
gitignored, so they exist only in the main checkout, and a pinned test command
naming any of them cannot run in the worktree at all.

Measured: a lead pinned `cd backend && source venv/bin/activate && pytest …`
with a baseline of "2649 passed". `venv/` is gitignored. The delegate found the
activate script absent, fell back to the login shell's `python`, and reported
**2511 passed / 137 skipped** as its green. Nothing lied — but a different
interpreter's green and the repo's green are not the same claim, and 137 skips
is what missing extras look like when nobody compares the totals. The lead's
own re-run in the worktree then failed at *collection* until `.env` was copied
in, which no test output would have explained.

So, before dispatch: run the pinned command in the worktree yourself, provision
what it needs, and say in the task prompt which interpreter to use. And when a
delegate reports a suite total, **compare it to your baseline digit for
digit** — a total that differs by more than the tests you added is a different
environment, not a different result.

**Matching totals do not clear it, though — the same test can pass there and
fail here.** Measured 2026-08-30: a delegate reported "3 passed" for the three
tests the lead was measuring as failed on the same commit, with identical suite
totals. The sandbox has no network; the test's mock was unbound, so the real
function underneath reached out, raised, and the code returned a fallback the
assertion accepted. No error, no skip, nothing for a totals diff to catch. When
a round turns on specific tests, **re-run those tests yourself** rather than
diffing counts — and treat the divergence as a finding in its own right, since
a test whose verdict depends on the runner having network is a test hitting
live network.

**Never provision a shared directory by SYMLINKING the lead's copy into the
delegate's worktree.** It looks like the cheap answer for a 700-package
`node_modules` and it puts the lead's own installation inside the delegate's
blast radius. Measured 2026-08-30, twice in one day, from a single symlink:

- The delegate's `git add` swept the LINK into its feature commit —
  `.gitignore`'s `node_modules/` (trailing slash) matches directories, not
  symlinks — and the merge then replaced the real directory with a
  self-pointing broken link in the main checkout.
- Both delegates independently judged the link "broken" and replaced it with
  their own install. The `rm -rf` went THROUGH the link and gutted the LEAD's
  `node_modules`: 645 of 693 entries left as empty directories, `.bin` empty.
  Nothing in git was lost, and nothing announced itself either — it surfaced
  an hour later as a launcher that could not find its own binary.

Give each worktree a real directory: its own `npm ci`/`uv sync` (slow, always
correct), or a hardlink copy (`cp -al`) if the ecosystem tolerates it —
separate directory entries, so a delete cannot reach back. And note that this
is not just an efficiency trade: two delegates that "fixed" the link both
produced test runs against an install the lead never verified, which is the
same class of false green as the interpreter mismatch above.

**"Provision" is not "copy the real one."** The untracked file the suite wants
is very often the one holding every credential the project has, and a write
delegate has its whole worktree inside its sandbox — so copying it in hands a
third-party model your API keys, your database URL, and, in the repo this was
measured on, a **broker** key that moves real money. Twenty-two credential keys
in one `.env`. Never that.

The split that keeps both halves honest:

- **Delegate worktree** — the minimum sanitized config, and nothing that is
  secret. Better still, let the delegate mint its own: measured on the same
  run, the delegate hit the missing config, set a throwaway
  `SECRET_KEY='test-only-<slug>'` inline, and completed 2511 tests without ever
  needing a real value. The safe path is not a compromise here; it is what
  actually happened, unprompted.
- **The lead's own verification run** — the real file is fine. The lead already
  holds these credentials; using them is not an exposure. This is the run whose
  total is authoritative anyway.

If the suite genuinely cannot start without a real secret, that is a finding
about the repo's test setup, not a reason to ship the secret into a sandbox.
