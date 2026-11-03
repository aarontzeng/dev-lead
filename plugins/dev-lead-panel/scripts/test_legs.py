"""The argv shapes dev-lead's data/launch.json gives each leg, and look-alikes that are not legs."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import legs  # noqa: E402

LEGS = [
    (["codex", "exec", "-C", "/t", "-s", "read-only", "-m", "gpt-6.1-terra", "-c",
      "model_reasoning_effort=medium", "-o", "/r/review.md", "--", "-"],
     ("codex", "review", "gpt-6.1-terra", "medium")),
    (["node", "/p/codex-companion.mjs", "task", "--background", "--write", "--fresh", "--model",
      "gpt-6.1-terra", "--effort", "high", "TASK"], ("codex", "implement", "gpt-6.1-terra", "high")),
    (["node", "/x/bin/codex.js", "exec", "-s", "read-only", "-m", "gpt-6.1-terra"],
     ("codex", "review", "gpt-6.1-terra", "")),
    (["agy", "-p", "PROMPT", "--model", "gemini-3.8-flash-medium", "--mode", "plan", "--sandbox"],
     ("agy", "review", "gemini-3.8-flash-medium", "")),
    (["/usr/bin/env", "python3", "./agy", "-p", "P", "--model", "gemini-3.8-flash-high", "--mode", "plan"],
     ("agy", "review", "gemini-3.8-flash-high", "")),
    (["agy", "-p", "PROMPT", "--model", "gemini-3.8-flash-high", "--mode", "accept-edits"],
     ("agy", "implement", "gemini-3.8-flash-high", "")),
    (["bash", "/h/.local/share/cursor-agent/versions/v/cursor-agent", "-p", "--mode", "ask", "--trust",
      "--model", "composer-2.5", "PROMPT"], ("cursor", "review", "composer-2.5", "")),
    (["opencode", "run", "--print-logs", "--log-level", "info", "-m", "opencode-go/glm-5.3-flash#high"],
     ("opencode", "", "opencode-go/glm-5.3-flash", "high")),
    (["claude", "-p", "--permission-mode", "plan", "--model", "claude-sonnet-5-5", "--effort", "xhigh",
      "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
     ("claude", "review", "claude-sonnet-5-5", "xhigh")),
    (["claude", "-p", "TASK", "--permission-mode", "acceptEdits", "--model", "claude-opus-5-5", "--effort",
      "xhigh", "--settings", '{"disableAllHooks": true}'], ("claude", "implement", "claude-opus-5-5", "xhigh")),
    (["grok", "--prompt-file", "/r/p.md", "-m", "grok-4.6", "--effort", "high", "--sandbox", "read-only"],
     ("grok", "review", "grok-4.6", "high")),
]

NOT_LEGS = [
    ["agy"],
    ["cursor-agent"],
    ["opencode", "--standalone"],
    ["claude", "-p", "summarize this session"],
    ["claude", "-p", "ask", "--settings", "{}"],
    ["claude"],
    ["bash", "/tmp/cursor-agent-helper", "-p"],
    ["bash", "/home/me/cursor-agent/test.sh", "-p"],
    ["python3", "/q/cursor-agent/versions/2026/rename-tool.py", "-p", "x"],
]


class Classify(unittest.TestCase):
    def test_every_launch_shape_is_a_leg(self):
        for argv, want in LEGS:
            self.assertEqual(legs.classify(argv), want, argv)

    def test_look_alikes_are_not_legs(self):
        for argv in NOT_LEGS:
            self.assertIsNone(legs.classify(argv), argv)


class PromptFlags(unittest.TestCase):
    """A prompt split into words (the macOS ps fallback) must not supply a shown value."""

    def test_a_flag_given_twice_is_not_trusted(self):
        argv = ["agy", "-p", "look", "--model", "gpt-secret", "--mode", "plan", "now",
                "--model", "gemini-3.8-flash-high", "--mode", "accept-edits"]
        self.assertEqual(legs.classify(argv), ("agy", "?", "?", ""))

    def test_a_claude_effort_given_twice_is_not_trusted(self):
        argv = ["claude", "-p", "x", "--effort", "secret", "--permission-mode", "acceptEdits", "--model",
                "claude-opus-5-5", "--effort", "xhigh", "--settings", "{}"]
        self.assertEqual(legs.classify(argv)[3], "?")


    def test_a_codex_prompt_argument_is_not_an_effort(self):
        argv = ["codex", "exec", "-s", "read-only", "-m", "gpt-6.1-terra", "model_reasoning_effort=secret"]
        self.assertEqual(legs.classify(argv)[3], "")

    def test_a_codex_effort_given_twice_is_not_trusted(self):
        argv = ["codex", "exec", "-s", "read-only", "-m", "gpt-6.1-terra", "-c", "model_reasoning_effort=low",
                "-c", "model_reasoning_effort=xhigh"]
        self.assertEqual(legs.classify(argv)[3], "?")

    def test_a_claude_implement_leg_with_a_forged_mode_stays_listed(self):
        argv = ["claude", "-p", "x", "--permission-mode", "plan", "--permission-mode", "acceptEdits",
                "--model", "claude-opus-5-5", "--settings", "{}"]
        self.assertEqual(legs.classify(argv)[:2], ("claude", "?"))


class Shown(unittest.TestCase):
    PATS = ["gpt-", "gemini", "glm", "claude", "composer"]

    def test_only_family_known_identifiers_are_shown(self):
        self.assertEqual(legs.shown_model("gpt-6.1-terra", self.PATS), "gpt-6.1-terra")
        self.assertEqual(legs.shown_model("SECRET", self.PATS), "?")
        self.assertEqual(legs.shown_model("gpt- leaked words", self.PATS), "?")

    def test_effort_is_a_short_word(self):
        self.assertEqual(legs.shown_effort("xhigh"), "xhigh")
        self.assertEqual(legs.shown_effort("x y"), "?")
        self.assertEqual(legs.shown_effort(""), "")

    @unittest.skipIf(sys.platform == "win32", "the ps fallback is macOS-only")
    def test_a_prompt_line_cannot_become_a_ps_row(self):
        import subprocess
        real = subprocess.run
        listing = "  10 1 01:02 /usr/local/bin/agy\n  11 1 00:05 /bin/zsh\n"
        args = {"10": "agy -p first line\n123 1 12:34 agy -p x --model gpt-y\n"}

        def fake(argv, **_):
            if "-p" in argv:
                return subprocess.CompletedProcess(argv, 0, args[argv[argv.index("-p") + 1]], "")
            return subprocess.CompletedProcess(argv, 0, listing, "")
        subprocess.run = fake
        try:
            rows = list(legs.procs_ps())
        finally:
            subprocess.run = real
        self.assertEqual([(r[0], r[2]) for r in rows], [(10, 62)])


class MacLayouts(unittest.TestCase):
    def test_procargs2_keeps_each_argument_whole(self):
        import struct
        args = [b"agy", b"-p", b"look --model gpt-secret\nline two", b"--model", b"gemini-3.8-flash-high"]
        raw = struct.pack("<i", len(args)) + b"/usr/local/bin/agy\0\0\0\0" + b"\0".join(args) + b"\0PATH=/bin\0"
        argv = legs.parse_procargs2(raw)
        self.assertEqual(argv[2], "look --model gpt-secret\nline two")
        self.assertEqual(legs.classify(argv)[2], "gemini-3.8-flash-high")

    def test_procargs2_with_fewer_strings_than_argc_is_empty(self):
        import struct
        self.assertEqual(legs.parse_procargs2(struct.pack("<i", 3) + b"/bin/x\0\0a\0"), [])

    def test_bsdinfo_fields(self):
        import struct
        raw = bytearray(legs.BSDINFO_SIZE)
        struct.pack_into("<III", raw, 12, 501, 1, 502)
        struct.pack_into("<QQ", raw, 120, 1700000000, 250)
        raw[48:52] = b"agy\0"
        self.assertEqual(legs.parse_bsdinfo(bytes(raw)), (501, 1, 502, 1700000000, 250, b"agy"))

    def test_procargs2_with_an_unterminated_last_argument_is_empty(self):
        import struct
        self.assertEqual(legs.parse_procargs2(struct.pack("<i", 2) + b"/bin/agy\0\0agy\0-p"), [])


class MacMain(unittest.TestCase):
    def test_macos_shows_full_values_from_the_kernel_argv(self):
        procs = [(10, 1, 30, ["agy", "-p", "P", "--model", "gemini-3.8-flash-high", "--mode", "plan"], "/w/t")]
        import io, json
        real = (legs.procs_macos, legs.model_patterns, legs.os.path.isdir, sys.stdout, sys.platform)
        legs.procs_macos, legs.model_patterns = (lambda: iter(procs)), (lambda: ["gemini"])
        legs.os.path.isdir = lambda p: False
        sys.platform, sys.stdout = "darwin", io.StringIO()
        try:
            legs.main()
            out = json.loads(sys.stdout.getvalue())
        finally:
            legs.procs_macos, legs.model_patterns, legs.os.path.isdir, sys.stdout, sys.platform = real
        self.assertEqual(out["legs"], [{"adapter": "agy", "role": "review", "model": "gemini-3.8-flash-high",
                                        "effort": "", "secs": 30, "dir": "t"}])


@unittest.skipUnless(sys.platform == "win32", "shell32 is Windows-only")
class WindowsSplitter(unittest.TestCase):
    def test_quoted_and_escaped_arguments_stay_whole(self):
        line = r'agy.exe -p "look --model gpt-secret \"x\" y" --model gemini-3.8-flash-high --mode plan'
        argv = legs.split_windows(line)
        self.assertEqual(argv[2], 'look --model gpt-secret "x" y')
        self.assertEqual(legs.classify(argv), ("agy", "review", "gemini-3.8-flash-high", ""))


class Windows(unittest.TestCase):
    def test_powershell_output_is_read_as_utf8_and_a_single_row_is_a_list(self):
        import subprocess
        seen = {}
        row = '{"ProcessId":7,"ParentProcessId":1,"CreationDate":"/Date(0)/","CommandLine":"codex exec \u4e2d"}'

        def fake(argv, **kw):
            seen.update(kw, cmd=argv[-1])
            return subprocess.CompletedProcess(argv, 0, row, "")
        real = subprocess.run, legs.split_windows
        subprocess.run, legs.split_windows = fake, (lambda line: line.split())
        try:
            rows = list(legs.procs_windows())
        finally:
            subprocess.run, legs.split_windows = real
        self.assertEqual((seen["encoding"], seen["errors"]), ("utf-8", "replace"))
        self.assertIn("OutputEncoding = [Text.Encoding]::UTF8", seen["cmd"])
        self.assertEqual([r[0] for r in rows], [7])
        self.assertEqual(rows[0][3], ["codex", "exec", "\u4e2d"])


class Main(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir("/proc/self"), "the /proc branch")
    def test_a_leg_under_a_wrapper_of_its_adapter_is_one_row(self):
        agy = ["agy", "-p", "P", "--model", "gemini-3.8-flash-high", "--mode", "plan"]
        procs = [(10, 1, 30, agy, "/w/a"), (11, 10, 29, [], ""), (12, 11, 29, agy, "/w/a"),
                 (20, 1, 5, ["opencode", "run", "-m", "opencode-go/glm-5.3-flash#high"], "/w/b")]
        import io, json
        real = (legs.procs_linux, legs.model_patterns, sys.platform, sys.stdout)
        legs.procs_linux, legs.model_patterns = (lambda: iter(procs)), (lambda: ["gemini", "glm"])
        sys.stdout = io.StringIO()
        try:
            legs.main()
            out = json.loads(sys.stdout.getvalue())
        finally:
            legs.procs_linux, legs.model_patterns, _, sys.stdout = real
        self.assertEqual([(r["adapter"], r["secs"], r["dir"]) for r in out["legs"]], [("agy", 30, "a"), ("opencode", 5, "b")])


    def test_without_proc_only_adapter_and_age_are_shown(self):
        # A newline in a macOS prompt can cut the real flags off the first line of ps output.
        procs = [(10, 1, 30, ["agy", "-p", "look", "--model", "gpt-secret"], "")]
        import io, json
        real = (legs.procs_ps, legs.model_patterns, legs.os.path.isdir, sys.stdout, sys.platform)
        legs.procs_ps, legs.model_patterns = (lambda: iter(procs)), (lambda: ["gpt-"])
        legs.os.path.isdir = lambda p: False
        sys.platform = "darwin"
        legs.os.environ["QUOTA_PANEL_LEGS_PS"] = "1"
        sys.stdout = io.StringIO()
        try:
            legs.main()
            out = json.loads(sys.stdout.getvalue())
        finally:
            legs.procs_ps, legs.model_patterns, legs.os.path.isdir, sys.stdout, sys.platform = real
            legs.os.environ.pop("QUOTA_PANEL_LEGS_PS", None)
        self.assertEqual(out["legs"], [{"adapter": "agy", "role": "", "model": "", "effort": "", "secs": 30, "dir": ""}])


if __name__ == "__main__":
    unittest.main()
