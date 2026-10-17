# tests/test_packages.py
import inspect
import io
import re
import shutil
import tokenize
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from lib import deploy, generate, packages

ROOT = Path(__file__).resolve().parent.parent

# Everything that could run a command on the user's machine. Panel.qml is in
# here because a Quickshell `Process` is as real a shell as subprocess is.
EXECUTABLE_SOURCES = (
    sorted((ROOT / "lib").glob("*.py"))
    + [ROOT / "omarchy.ime", ROOT / "Panel.qml"]
    + sorted((ROOT / "payload").rglob("*.lua"))
)


class FakeRunner:
    """Answers a command by matching prefix, as (code, stdout, stderr)."""

    def __init__(self, results=None, default=(0, "", "")):
        self.results = results or {}
        self.default = default
        self.calls = []

    def __call__(self, command):
        command = list(command)
        self.calls.append(command)
        joined = " ".join(command)
        for prefix, result in self.results.items():
            if joined.startswith(prefix):
                return result
        return self.default

    def joined(self):
        return [" ".join(call) for call in self.calls]


class MissingTests(unittest.TestCase):
    def test_nothing_missing_when_all_present(self):
        self.assertEqual(packages.missing(FakeRunner()), [])

    def test_only_the_absent_package_is_reported(self):
        # `omarchy pkg present` exits non-zero when *any* of the names it is
        # handed is absent, so asking about a whole group and reporting the
        # whole group back named two packages the user has installed. One
        # question per package is what makes the answer per package.
        runner = FakeRunner({"omarchy pkg present fcitx5-rime": (1, "", "")})
        self.assertEqual(packages.missing(runner), ["fcitx5-rime"])

    def test_every_package_is_asked_about_on_its_own(self):
        runner = FakeRunner()
        packages.missing(runner)
        self.assertEqual(
            runner.joined(),
            [f"omarchy pkg present {name}"
             for name in packages.REQUIRED + packages.AUR],
        )

    def test_absent_packages_are_reported(self):
        runner = FakeRunner(default=(1, "", ""))
        self.assertEqual(packages.missing(runner), packages.REQUIRED + packages.AUR)


class CheckTests(unittest.TestCase):
    """A question that could not be asked is not an answer of "no"."""

    def test_a_command_that_cannot_run_reports_no_missing_packages(self):
        runner = FakeRunner(default=(packages.UNAVAILABLE, "", "no such file"))
        self.assertEqual(packages.check(runner)["missing"], [])

    def test_a_command_that_cannot_run_says_why(self):
        runner = FakeRunner(default=(packages.UNAVAILABLE, "", "no such file"))
        self.assertIn("no such file", packages.check(runner)["error"])

    def test_a_reachable_command_reports_no_error(self):
        self.assertEqual(packages.check(FakeRunner())["error"], "")


class RunnerTests(unittest.TestCase):
    """The one unguarded subprocess in the plugin, on the panel's poll path."""

    def test_a_missing_command_does_not_raise(self):
        with mock.patch.object(subprocess, "run",
                               side_effect=FileNotFoundError("omarchy")):
            code, _, stderr = packages._run(["omarchy", "pkg", "present", "fcitx5"])
        self.assertIs(code, packages.UNAVAILABLE)
        self.assertIn("omarchy", stderr)

    def test_a_command_that_hangs_does_not_hang_the_panel(self):
        with mock.patch.object(
                subprocess, "run",
                side_effect=subprocess.TimeoutExpired("omarchy", 30)):
            code, _, _ = packages._run(["omarchy", "pkg", "present", "fcitx5"])
        self.assertIs(code, packages.UNAVAILABLE)

    def test_stderr_is_carried_out(self):
        completed = subprocess.CompletedProcess([], 1, "out", "the real reason")
        with mock.patch.object(subprocess, "run", return_value=completed):
            code, stdout, stderr = packages._run(["omarchy", "pkg", "add", "fcitx5"])
        self.assertEqual((code, stdout, stderr), (1, "out", "the real reason"))

    def test_the_call_is_bounded_by_a_timeout(self):
        with mock.patch.object(subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            packages._run(["omarchy", "pkg", "present", "fcitx5"])
        self.assertEqual(run.call_args.kwargs["timeout"], packages.COMMAND_TIMEOUT)


class InstallScriptTests(unittest.TestCase):
    """What the terminal is asked to run."""

    def shell(self, *arguments):
        """Run `sh` with the given arguments; the script is the last one."""
        return subprocess.run(
            [shutil.which("sh") or "/bin/sh", *arguments[:-1], "-c", arguments[-1]],
            capture_output=True, text=True,
        )

    def test_official_packages_go_to_the_pacman_wrapper(self):
        script = packages.install_script(["fcitx5", "fcitx5-rime"])
        self.assertIn("omarchy-pkg-add fcitx5 fcitx5-rime", script)

    def test_aur_packages_go_to_the_aur_wrapper(self):
        script = packages.install_script(["rime-ice-git"])
        self.assertIn("omarchy-pkg-aur-add rime-ice-git", script)

    def test_the_aur_is_checked_before_it_is_used(self):
        script = packages.install_script(["rime-ice-git"])
        self.assertLess(script.index("omarchy-pkg-aur-accessible"),
                        script.index("omarchy-pkg-aur-add"))

    def test_nothing_to_install_is_an_empty_script(self):
        self.assertEqual(packages.install_script([]), "")

    def test_the_script_is_valid_shell(self):
        result = self.shell(
            "-n", packages.install_script(packages.REQUIRED + packages.AUR))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_failed_pacman_step_does_not_blame_the_aur(self):
        # `&&` and `||` associate left to right with equal precedence, so an
        # ungrouped `add && accessible || { aur message } && aur-add` reports
        # the AUR as unreachable when it was pacman that failed - and runs the
        # AUR install anyway when both succeed.
        script = packages.install_script(packages.REQUIRED + packages.AUR)
        script = script.replace("omarchy-pkg-add", "false", 1)
        result = self.shell(script)
        self.assertNotIn("AUR", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    def test_an_unreachable_aur_is_not_followed_by_the_aur_install(self):
        script = packages.install_script(packages.AUR)
        script = script.replace("omarchy-pkg-aur-accessible", "false", 1)
        script = script.replace("omarchy-pkg-aur-add",
                                "echo SHOULD-NOT-RUN #", 1)
        result = self.shell(script)
        self.assertIn("The AUR is not reachable", result.stdout)
        self.assertNotIn("SHOULD-NOT-RUN", result.stdout)
        self.assertEqual(result.returncode, 1)


class InstallTests(unittest.TestCase):
    """The panel's install has to run somewhere sudo can ask for a password."""

    def test_the_work_is_handed_to_a_terminal(self):
        runner = FakeRunner()
        started, _ = packages.install(["fcitx5"], runner)
        self.assertTrue(started)
        self.assertEqual(runner.calls[0][0], packages.TERMINAL_LAUNCHER)

    def test_no_sudo_is_run_where_nothing_can_answer_it(self):
        runner = FakeRunner()
        packages.install(["fcitx5", "rime-ice-git"], runner)
        for call in runner.calls:
            self.assertNotIn("sudo", call)
            self.assertNotEqual(call[0], "omarchy")

    def test_the_detail_says_the_terminal_is_doing_it(self):
        started, detail = packages.install(["fcitx5"], FakeRunner())
        self.assertTrue(started)
        self.assertIn("terminal", detail)

    def test_a_launcher_that_fails_carries_its_stderr(self):
        runner = FakeRunner(default=(1, "", "no terminal is configured"))
        started, detail = packages.install(["fcitx5"], runner)
        self.assertFalse(started)
        self.assertIn("no terminal is configured", detail)

    def test_a_launcher_that_cannot_be_run_says_so(self):
        runner = FakeRunner(default=(packages.UNAVAILABLE, "", "no such file"))
        started, detail = packages.install(["fcitx5"], runner)
        self.assertFalse(started)
        self.assertIn("no such file", detail)


class InstallHereTests(unittest.TestCase):
    """The command-line path, where this process already owns a terminal."""

    def test_official_and_aur_go_to_different_commands(self):
        calls = []
        packages.install_here(["fcitx5", "rime-ice-git"],
                              lambda command: calls.append(list(command)) or 0)
        joined = [" ".join(call) for call in calls]
        self.assertIn("omarchy pkg add fcitx5", joined)
        self.assertIn("omarchy pkg aur add rime-ice-git", joined)

    def test_aur_is_skipped_when_unreachable(self):
        calls = []

        def runner(command):
            calls.append(list(command))
            return 1 if "accessible" in command else 0

        ok, detail = packages.install_here(["rime-ice-git"], runner)
        self.assertFalse(ok)
        self.assertNotIn("omarchy pkg aur add rime-ice-git",
                         [" ".join(call) for call in calls])
        self.assertIn("AUR", detail)

    def test_installing_nothing_succeeds_without_calling_out(self):
        calls = []
        ok, _ = packages.install_here([], lambda command: calls.append(command) or 0)
        self.assertTrue(ok)
        self.assertEqual(calls, [])

    def test_output_is_not_captured_so_sudo_can_prompt(self):
        with mock.patch.object(subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0)
            packages._run_attached(["omarchy", "pkg", "add", "fcitx5"])
        self.assertNotIn("capture_output", run.call_args.kwargs)

    def test_a_command_that_cannot_be_run_is_not_a_success(self):
        ok, _ = packages.install_here(["fcitx5"],
                                      lambda command: packages.UNAVAILABLE)
        self.assertFalse(ok)




class PrivilegeSurfaceTests(unittest.TestCase):
    """What this plugin may do to the machine, asserted rather than said.

    Two of these answer a marketplace scanner that read a *comment* about
    /etc/sudoers.d and a read-only `systemctl is-active` as capabilities the
    plugin does not have. They are kept as tests rather than as prose, because a
    comment saying "we never touch sudoers" is worth nothing the day somebody
    does - and because a claim about code should be checked against code.

    Which is why every check here runs over source with its comments and
    docstrings removed. A scanner that reads prose as behaviour produces exactly
    the false positives this class exists to close, and repeating that mistake
    here would only move it somewhere else.
    """

    TRIPLE = ('"' * 3, "'" * 3)

    @classmethod
    def _code(cls, path: Path, body: str) -> str:
        """`body` with comments and docstrings gone. String literals stay."""
        if path.suffix == ".py" or path.name == "omarchy.ime":
            kept = []
            try:
                for token in tokenize.generate_tokens(io.StringIO(body).readline):
                    if token.type == tokenize.COMMENT:
                        continue
                    if (token.type == tokenize.STRING
                            and token.string.lstrip("rbfuRBFU")[:3] in cls.TRIPLE):
                        continue
                    kept.append(token.string)
            except (tokenize.TokenError, IndentationError, SyntaxError):
                return body
            return "\n".join(kept)
        if path.suffix == ".qml":
            body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S)
            return re.sub(r"//[^\n]*", " ", body)
        if path.suffix == ".lua":
            body = re.sub(r"--\[\[.*?\]\]", " ", body, flags=re.S)
            return re.sub(r"--[^\n]*", " ", body)
        return body

    def _sources(self):
        for path in EXECUTABLE_SOURCES:
            if path.is_file():
                raw = path.read_text(encoding="utf-8", errors="replace")
                yield path, self._code(path, raw)

    def test_the_comment_stripper_actually_strips(self):
        # This class is only as good as this. A stripper that quietly returned
        # its input would turn every check below into a green tick.
        stripped = self._code(Path("x.py"), '# sudoers\ny = "kept"\n')
        self.assertNotIn("sudoers", stripped)
        self.assertIn("kept", stripped)

    def test_every_executable_source_is_actually_scanned(self):
        # A glob that quietly matched nothing would be the other way to make
        # this class pass without checking anything.
        scanned = {path.name for path, _ in self._sources()}
        self.assertIn("packages.py", scanned)
        self.assertIn("deploy.py", scanned)
        self.assertIn("omarchy.ime", scanned)
        self.assertIn("Panel.qml", scanned)

    def test_nothing_writes_a_sudoers_policy(self):
        # No sudoers file, no drop-in directory, no visudo, in any code path.
        for path, body in self._sources():
            with self.subTest(path.name):
                self.assertNotIn("sudoers", body)
                self.assertNotIn("visudo", body)

    def test_the_only_system_path_named_anywhere_is_one_that_is_read(self):
        # /etc/vconsole.conf is read to find the console keymap. It is the only
        # path outside the user's own XDG directories this plugin names at all.
        found = set()
        for _, body in self._sources():
            found.update(re.findall(r"/etc/[A-Za-z0-9_./-]*", body))
        self.assertEqual(found, {"/etc/vconsole.conf"})

    def test_that_one_system_path_is_only_ever_read(self):
        source = inspect.getsource(generate.system_layout)
        self.assertIn("read_text", source)
        for writing in ("write_text", "write_bytes", "unlink", "mkdir", "chmod"):
            self.assertNotIn(writing, source)

    def test_the_only_systemd_command_is_a_read(self):
        # `is-active` asks; start, stop, restart, enable, disable, mask and
        # daemon-reload tell. fcitx5's lifecycle belongs to the session that
        # launched it, and a plugin that could take it over is a plugin that
        # could leave someone unable to type.
        commanding = re.compile(
            r"systemctl[^\n]*\b(start|stop|restart|reload|enable|disable|mask|"
            r"unmask|kill|set-property|daemon-reload)\b")
        for path, body in self._sources():
            with self.subTest(path.name):
                self.assertIsNone(commanding.search(body), path)

    def test_the_state_query_is_named_at_its_call_site(self):
        self.assertEqual(deploy.UNIT_STATE_QUERY,
                         ["systemctl", "--user", "is-active", deploy.UNIT])

    # A privileged command being *invoked*: the name in command position - the
    # first word of a string, a list element, or the head of a shell clause.
    # Not the word appearing in a sentence, which is what `Panel.qml` does when
    # it tells the user which prompt to expect. That distinction is the whole
    # subject of this class.
    ELEVATES = re.compile(
        r"""(?:^|["'\[(,;&]|\|\||&&|&)\s*(sudo|pkexec|pacman|doas)\b""", re.M)

    def test_the_invocation_pattern_tells_a_command_from_a_sentence(self):
        self.assertIsNotNone(self.ELEVATES.search('["sudo", "pacman", "-S"]'))
        self.assertIsNotNone(self.ELEVATES.search('run("pkexec foo")'))
        self.assertIsNone(
            self.ELEVATES.search('"a terminal window, where sudo can ask"'))

    def test_elevation_only_ever_goes_through_omarchys_own_command(self):
        # `omarchy pkg add` is the one privileged thing, and it is Omarchy's
        # command run the way a user would run it - never a pacman, sudo, doas
        # or pkexec invocation of the plugin's own.
        for path, body in self._sources():
            with self.subTest(path.name):
                found = self.ELEVATES.search(body)
                self.assertIsNone(found, f"{path}: {found.group(0) if found else ''}")

    def test_every_command_the_installer_runs_is_an_omarchy_command(self):
        # Both install paths, at runtime rather than by reading the source.
        wanted = packages.REQUIRED + packages.AUR
        calls = []

        packages.install(wanted, runner=lambda c: (calls.append(list(c)), (0, "", ""))[1])
        packages.install_here(wanted, runner=lambda c: (calls.append(list(c)), 0)[1])

        self.assertTrue(calls)
        for call in calls:
            with self.subTest(call):
                self.assertTrue(call[0].startswith("omarchy"), call)

    def test_the_script_handed_to_the_terminal_only_calls_omarchy_helpers(self):
        script = packages.install_script(packages.REQUIRED + packages.AUR)
        self.assertIsNone(self.ELEVATES.search(script), script)
        for step in script.split("&&"):
            head = step.strip().lstrip("{ ").split(" ", 1)[0]
            with self.subTest(step.strip()):
                self.assertTrue(head.startswith(("omarchy", "echo")), step)


if __name__ == "__main__":
    unittest.main()
