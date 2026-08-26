"""What Rime itself makes of a deployed configuration.

Every other test in this suite reads what the plugin wrote. This one hands the
written tree to `rime_deployer --build`, the same compiler fcitx5-rime runs,
and reads what came out: a configuration that fails to build takes the input
method down, and the five bugs this plugin shipped through a green suite were
all of that kind. It runs only where librime and the packaged rime-ice data
are installed, which is every machine that can run the plugin at all.
"""
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import deploy, settings, theme

ROOT = Path(__file__).resolve().parent.parent
SHARED = Path("/usr/share/rime-data")
DEPLOYER = shutil.which("rime_deployer")

PALETTE = {
    "accent": "#88c0d0", "selection": "#434c5e", "muted": "#616e88",
    "background": "#2e3440", "dark_background": "#242933",
    "lighter_background": "#3b4252", "foreground": "#d8dee9",
}

# The filter as librime writes it into the built schema: the `*` marks the
# module as one loaded from the user directory's lua/ tree.
FILTER = "lua_filter@*annotate"


@unittest.skipUnless(DEPLOYER and (SHARED / "rime_ice.schema.yaml").is_file(),
                     "needs rime_deployer and the packaged rime-ice data")
class RimeBuildTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.rime = root / "rime"
        self.targets = {"rime": self.rime, "fcitx5": root / "fcitx5",
                        "themes": root / "themes"}
        self.state = root / "state"
        self.addCleanup(self._tmp.cleanup)

    def _deploy(self, **overrides):
        deploy.deploy(
            settings.resolve(overrides), self.targets, self.state, ROOT / "payload",
            asset_provider=lambda name: None,
            dictionary_path=str(self.rime.parent / "cache" / "cedict.txt"),
            theme_source=lambda: theme.resolved(PALETTE, 12, "nord"),
        )

    def _build(self) -> tuple[subprocess.CompletedProcess, list[str]]:
        build = self.rime / "build"
        result = subprocess.run(
            [DEPLOYER, "--build", str(self.rime), str(SHARED), str(build)],
            capture_output=True, text=True, timeout=300,
        )
        log = result.stdout + result.stderr
        errors = [line for line in log.splitlines()
                  if any(marker in line for marker in deploy.BUILD_ERRORS)]
        return result, errors

    def _built_schema(self) -> str:
        path = self.rime / "build" / "rime_ice.schema.yaml"
        self.assertTrue(path.is_file(), "Rime did not write the built schema")
        return path.read_text(encoding="utf-8")

    def test_the_default_configuration_builds(self):
        self._deploy()
        result, errors = self._build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(errors, [])
        self.assertNotIn(FILTER, self._built_schema())

    def test_annotation_on_builds_with_the_filter_in_the_schema(self):
        self._deploy(annotation=True)
        result, errors = self._build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(errors, [])
        built = self._built_schema()
        self.assertEqual(built.count(FILTER), 1)
        self.assertIn("name: annotate", built)
        self.assertIn("toggle: annotate", built)

    def test_annotation_off_builds_without_a_trace_of_it(self):
        self._deploy(annotation=False)
        result, errors = self._build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(errors, [])
        self.assertNotIn("annotate", self._built_schema())

    def test_switching_it_off_after_a_build_with_it_on_rebuilds_clean(self):
        # The sequence a user produces: on, deploy, off, deploy. Rime rebuilds
        # from what is on disk now, so nothing of the first build may linger.
        self._deploy(annotation=True)
        self.assertEqual(self._build()[1], [])
        self.assertIn(FILTER, self._built_schema())
        self._deploy(annotation=False)
        result, errors = self._build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(errors, [])
        self.assertNotIn("annotate", self._built_schema())
        self.assertFalse((self.rime / "lua" / "annotate.lua").exists())


if __name__ == "__main__":
    unittest.main()
