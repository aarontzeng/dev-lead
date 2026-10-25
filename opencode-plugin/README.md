# opencode-plugin/go-usage

An opencode 2 plugin that polls the opencode Go usage endpoint every 10 minutes
(first poll 5 s after the plugin is loaded (opencode starts its own server)) and keeps the answer in
`~/.cache/opencode-go/usage.json` (owner-only, schema 1). It also toasts the
figures in the TUI.

It exists for `scripts/quota-snapshot.py`: that script never holds the Go key,
so it reads this cache. **This plugin is the only code in the suite that reads
the key** (from `OPENCODE_GO_API_KEY`, or `opencode-go` in opencode's
`auth.json`), sends it only to `https://opencode.ai/inference/go/v1/usage`, and
never writes it to the cache. The cache's `error` field holds fixed strings
only; the window fields (`status`, `percent`, `resetsAt`) are copied from the
endpoint's answer as received. With no key it stops and leaves the cache alone.
Known limit: two opencode processes polling at once can overwrite a newer
success with an older snapshot plus an error (each write is atomic, the
read-then-write is not); the next successful poll repairs it.

Install (opencode 2 only; a v1 opencode cannot load it):

    scripts/install-opencode-go-usage.sh

The script copies the three files to `~/.config/opencode/plugins/go-usage`. It
copies, not links, because the suite's own path changes with every version;
run it again after a dev-lead update to refresh the copy. It refuses to
replace a link or directory it did not make unless given `--force`, and then
removes a link (never its target) or renames a directory aside, never deletes it.

`quota-snapshot.py --refresh` on a host whose `quota-sources.json` sets
`"refresh": "tmux_spawn"` on the opencode-go source starts `opencode --standalone` (a TUI with a private server, so the plugin is
loaded fresh and polls 5 s later) so this plugin gets to poll when no TUI is open.
A plain TUI would attach to opencode's background service, whose already-loaded
plugin polls only every 10 minutes.
