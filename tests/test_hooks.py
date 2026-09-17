"""The Omarchy theme-set hook is part of this plugin's own lifecycle.

Nothing here goes near ~/.config: every path is injected, so the hook these
tests install lands in a temporary directory and the real theme-set directory
is never touched.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import deploy, hooks


class HookPathTests(unittest.TestCase):
    def test_the_hook_directory_is_omarchys_theme_set_drop_in(self):
        self.assertEqual(
            hooks.hook_dir(Path("/home/someone/.config")),
            Path("/home/someone/.config/omarchy/hooks/theme-set.d"),
        )

    def test_the_hook_is_named_after_the_plugin_not_after_the_hook(self):
        """The drop-in directory is shared, and `theme-set` is already taken."""
        self.assertEqual(hooks.HOOK_NAME, "eastgold15.omarchy.ime")
        self.assertNotEqual(hooks.HOOK_NAME, "theme-set")

    def test_the_hook_name_is_not_skipped_as_a_sample(self):
        """omarchy-hook skips every *.sample file in the directory."""
        self.assertFalse(hooks.HOOK_NAME.endswith(".sample"))


class HookScriptTests(unittest.TestCase):
    MANAGER = "/home/someone/.config/omarchy/plugins/eastgold15.omarchy.ime/ime-manager"

    def script(self, manager=MANAGER):
        return hooks.script(manager)

    def test_it_calls_theme_apply_and_nothing_heavier(self):
        text = self.script()
        self.assertIn("theme apply", text)
        self.assertNotIn(" deploy", text)

    def test_it_names_the_manager_by_absolute_path(self):
        self.assertIn(self.MANAGER, self.script())

    def test_a_manager_that_is_no_longer_there_exits_quietly(self):
        """A hook must never be the reason a theme change reports a failure."""
        self.assertIn("exit 0", self.script())

    def test_the_path_is_shell_quoted(self):
        text = hooks.script("/home/some one/plugins/eastgold15.omarchy.ime/ime-manager")
        self.assertIn("'/home/some one/plugins/eastgold15.omarchy.ime/ime-manager'", text)

    def test_it_is_a_bash_script(self):
        self.assertTrue(self.script().startswith("#!"))
        self.assertIn("bash", self.script().splitlines()[0])


class HookInstallTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.hooks = root / "config" / "omarchy" / "hooks" / "theme-set.d"
        self.state = root / "state"
        self.state.mkdir(parents=True)
        self.manager = root / "plugin" / "ime-manager"
        self.manager.parent.mkdir(parents=True)
        self.manager.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    def manifest(self) -> dict:
        """The paths the manifest records, per target.

        Read through deploy.read_manifest rather than out of the JSON, because
        what these tests are about is which files are recorded - not the shape
        of the record beside each one, which now also says whether the file was
        created or written over.
        """
        path = self.state / deploy.MANIFEST_NAME
        if not path.is_file():
            return {}
        return {target: sorted(entries)
                for target, entries in deploy.read_manifest(self.state).items()}

    def write_manifest(self, files: dict) -> None:
        (self.state / deploy.MANIFEST_NAME).write_text(
            json.dumps({"files": files}, indent=2) + "\n", encoding="utf-8"
        )

    def test_install_writes_the_script_into_the_theme_set_directory(self):
        path = hooks.install(self.hooks, self.manager, self.state)
        self.assertEqual(path, self.hooks / hooks.HOOK_NAME)
        self.assertTrue(path.is_file())
        self.assertIn("theme apply", path.read_text(encoding="utf-8"))

    def test_install_creates_the_directory_when_omarchy_has_not(self):
        self.assertFalse(self.hooks.exists())
        hooks.install(self.hooks, self.manager, self.state)
        self.assertTrue(self.hooks.is_dir())

    def test_the_installed_hook_is_executable(self):
        path = hooks.install(self.hooks, self.manager, self.state)
        self.assertTrue(os.access(path, os.X_OK))

    def test_install_records_the_hook_in_the_manifest(self):
        hooks.install(self.hooks, self.manager, self.state)
        self.assertEqual(self.manifest()[hooks.TARGET], [hooks.HOOK_NAME])

    def test_install_leaves_the_rest_of_the_manifest_alone(self):
        self.write_manifest({"rime": ["default.custom.yaml"]})
        hooks.install(self.hooks, self.manager, self.state)
        self.assertEqual(self.manifest()["rime"], ["default.custom.yaml"])
        self.assertEqual(self.manifest()[hooks.TARGET], [hooks.HOOK_NAME])

    def test_installing_twice_changes_nothing(self):
        hooks.install(self.hooks, self.manager, self.state)
        hooks.install(self.hooks, self.manager, self.state)
        self.assertEqual(self.manifest()[hooks.TARGET], [hooks.HOOK_NAME])

    def test_installed_reports_the_hook(self):
        self.assertFalse(hooks.installed(self.hooks))
        hooks.install(self.hooks, self.manager, self.state)
        self.assertTrue(hooks.installed(self.hooks))

    def test_a_damaged_manifest_is_treated_as_an_empty_one(self):
        (self.state / deploy.MANIFEST_NAME).write_text("{ not json", encoding="utf-8")
        hooks.install(self.hooks, self.manager, self.state)
        self.assertEqual(self.manifest()[hooks.TARGET], [hooks.HOOK_NAME])


class HookRemoveTests(HookInstallTests):
    def test_remove_deletes_the_script_and_forgets_it(self):
        hooks.install(self.hooks, self.manager, self.state)
        self.assertTrue(hooks.remove(self.hooks, self.state))
        self.assertFalse((self.hooks / hooks.HOOK_NAME).exists())
        self.assertNotIn(hooks.TARGET, self.manifest())

    def test_remove_leaves_the_rest_of_the_manifest_alone(self):
        self.write_manifest({"rime": ["default.custom.yaml"]})
        hooks.install(self.hooks, self.manager, self.state)
        hooks.remove(self.hooks, self.state)
        self.assertEqual(self.manifest()["rime"], ["default.custom.yaml"])

    def test_removing_a_hook_that_was_never_installed_is_not_a_failure(self):
        self.assertFalse(hooks.remove(self.hooks, self.state))

    def test_remove_never_touches_another_plugins_hook(self):
        self.hooks.mkdir(parents=True)
        neighbour = self.hooks / "theme-set"
        neighbour.write_text("#!/bin/bash\n", encoding="utf-8")
        hooks.install(self.hooks, self.manager, self.state)
        hooks.remove(self.hooks, self.state)
        self.assertTrue(neighbour.is_file())


class HookSyncTests(HookInstallTests):
    """A deploy rewrites the manifest from what it wrote, so the hook's own
    entry has to be re-asserted afterwards or an uninstall would walk past it.
    """

    def test_sync_records_a_hook_the_manifest_has_forgotten(self):
        hooks.install(self.hooks, self.manager, self.state)
        self.write_manifest({"rime": ["default.custom.yaml"]})
        hooks.sync(self.hooks, self.state)
        self.assertEqual(self.manifest()[hooks.TARGET], [hooks.HOOK_NAME])

    def test_sync_drops_an_entry_whose_hook_is_gone(self):
        self.write_manifest({hooks.TARGET: [hooks.HOOK_NAME]})
        hooks.sync(self.hooks, self.state)
        self.assertNotIn(hooks.TARGET, self.manifest())

    def test_sync_writes_nothing_when_there_is_no_hook_and_no_entry(self):
        hooks.sync(self.hooks, self.state)
        self.assertEqual(self.manifest(), {})


class UninstallRemovesTheHookTests(HookInstallTests):
    """The manifest is what uninstall walks, so recording the hook there is the
    whole reason it gets removed with everything else.
    """

    def test_uninstall_removes_the_recorded_hook(self):
        hooks.install(self.hooks, self.manager, self.state)
        result = deploy.uninstall(self.state, {hooks.TARGET: self.hooks})
        self.assertEqual(result["removed"][hooks.TARGET], [hooks.HOOK_NAME])
        self.assertFalse((self.hooks / hooks.HOOK_NAME).exists())


@unittest.skipUnless(shutil.which("bash"), "omarchy-hook runs a hook with bash")
class HookBehaviourTests(unittest.TestCase):
    """The hook, run the way omarchy-hook runs it: `bash <hook> <theme name>`.

    The manager it calls is a stub script in a temporary directory. Nothing
    real is invoked and nothing outside that directory is written.
    """

    STUB = (
        "#!{python}\n"
        "import sys\n"
        "open({record!r}, 'w').write(' '.join(sys.argv[1:]))\n"
        "raise SystemExit({code})\n"
    )

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.record = self.root / "argv"
        self.manager = self.root / "ime-manager"

    def write_manager(self, code=0, executable=True):
        self.manager.write_text(
            self.STUB.format(python=sys.executable, record=str(self.record), code=code),
            encoding="utf-8",
        )
        os.chmod(self.manager, 0o755 if executable else 0o644)

    def run_hook(self) -> subprocess.CompletedProcess:
        hook = self.root / hooks.HOOK_NAME
        hook.write_text(hooks.script(self.manager), encoding="utf-8")
        return subprocess.run(
            ["bash", str(hook), "osuki-dark"], capture_output=True, text=True,
        )

    def test_it_asks_the_manager_to_apply_the_theme(self):
        self.write_manager()
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertEqual(self.record.read_text(encoding="utf-8"), "theme apply")

    def test_a_manager_that_fails_does_not_fail_the_theme_change(self):
        """omarchy-hook prints "Hook failed" for any non-zero exit, and neither
        a closed Fcitx5 nor a stale plugin is worth saying on every switch."""
        self.write_manager(code=1)
        self.assertEqual(self.run_hook().returncode, 0)

    def test_a_manager_that_is_no_longer_there_is_not_an_error(self):
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertFalse(self.record.exists())

    def test_a_manager_that_is_not_executable_is_not_run(self):
        self.write_manager(executable=False)
        self.assertEqual(self.run_hook().returncode, 0)
        self.assertFalse(self.record.exists())

    def test_it_says_nothing_on_a_successful_run(self):
        self.write_manager()
        result = self.run_hook()
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
