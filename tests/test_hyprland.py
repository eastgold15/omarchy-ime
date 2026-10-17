"""Detecting the two Hyprland pieces the plugin reports but does not own.

Nothing here runs hyprctl: the runner is injected, and the configuration scan is
pointed at a temporary tree. Every failure mode a real machine has - hyprctl not
installed, a compositor that is not Hyprland, an option name Hyprland does not
know - is a fixture rather than a thing to be arranged for real.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import hyprland


class FakeHyprctl:
    """A stand-in for hyprctl, keyed by the subcommand it is asked for.

    An answer of None is what a missing binary looks like to the runner: the
    call did not happen at all, which is a different thing from a call that
    happened and failed.
    """

    def __init__(self, **answers):
        self.answers = answers
        self.commands = []

    def __call__(self, command):
        self.commands.append(list(command))
        return self.answers.get(command[1])


# What `hyprctl getoption -j decoration:blur:input_methods` prints on a machine
# where the rule is in effect, and where it is not. Read off a real Hyprland.
BLUR_ON = (0, '{"option": "decoration:blur:input_methods", "bool": true, "set": true }\n')
BLUR_OFF = (0, '{"option": "decoration:blur:input_methods", "bool": false, "set": false }\n')

# hyprctl answers an option it does not know with prose and exit status 0, so
# the exit status alone never says whether the answer is an answer.
NO_SUCH_OPTION = (0, "no such option\n")

# What every hyprctl subcommand prints outside a Hyprland session.
NOT_HYPRLAND = (1, "HYPRLAND_INSTANCE_SIGNATURE not set! (is hyprland running?)\n")


def binds(*entries) -> tuple[int, str]:
    """`hyprctl binds -j` output holding the given bind objects."""
    base = {
        "modmask": 0, "key": "", "keycode": 0, "submap": "",
        "dispatcher": "exec", "arg": "", "description": "", "has_description": False,
    }
    return 0, json.dumps([dict(base, **entry) for entry in entries])


class BlurTests(unittest.TestCase):
    """Whether the candidate window is blurred, asked of the compositor.

    The question is about the effective value, not about a file: the rule can
    come from hyprland.conf, from an Omarchy Lua module, or from a drop-in, and
    all three are the same answer to the user. Only the running compositor knows
    which one won.
    """

    def state(self, answer) -> str:
        return hyprland.blur(FakeHyprctl(getoption=answer))

    def test_a_rule_in_effect_reads_as_on(self):
        self.assertEqual(self.state(BLUR_ON), hyprland.ON)

    def test_no_rule_reads_as_off(self):
        self.assertEqual(self.state(BLUR_OFF), hyprland.OFF)

    def test_it_asks_for_the_option_hyprland_actually_has(self):
        runner = FakeHyprctl(getoption=BLUR_ON)
        hyprland.blur(runner)
        self.assertIn(hyprland.BLUR_OPTION, runner.commands[0])
        self.assertEqual(runner.commands[0][0], "hyprctl")

    def test_a_missing_hyprctl_is_unknown_rather_than_off(self):
        # No compositor to ask is not the same answer as a compositor that said
        # no, and reporting it as "off" would send the user to fix a file that
        # is already right.
        self.assertEqual(self.state(None), hyprland.UNKNOWN)

    def test_a_compositor_that_is_not_hyprland_is_unknown(self):
        self.assertEqual(self.state(NOT_HYPRLAND), hyprland.UNKNOWN)

    def test_an_option_hyprland_does_not_know_is_unknown(self):
        # hyprctl exits 0 here, so this is only visible by reading the output.
        self.assertEqual(self.state(NO_SUCH_OPTION), hyprland.UNKNOWN)

    def test_output_that_is_not_json_is_unknown(self):
        self.assertEqual(self.state((0, "bool: true\nset: true\n")), hyprland.UNKNOWN)

    def test_json_without_a_value_is_unknown(self):
        self.assertEqual(self.state((0, '{"option": "x"}')), hyprland.UNKNOWN)

    def test_empty_output_is_unknown(self):
        self.assertEqual(self.state((0, "")), hyprland.UNKNOWN)

    def test_the_integer_spelling_of_a_boolean_is_read_too(self):
        # Hyprland reports some booleans as `int`. Reading only `bool` would
        # turn a rule that is on into "cannot tell".
        self.assertEqual(self.state((0, '{"option": "x", "int": 1}')), hyprland.ON)
        self.assertEqual(self.state((0, '{"option": "x", "int": 0}')), hyprland.OFF)


class ToggleBindTests(unittest.TestCase):
    """Whether some key toggles the input method, asked of the compositor.

    The modifier and the key are the user's business: what is detected is the
    dispatch, so a user who bound it to something other than a left-Shift tap
    still reads as having it.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.config = Path(self._tmp.name) / "hypr"
        self.config.mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def state(self, answer) -> str:
        return hyprland.toggle(FakeHyprctl(binds=answer), config_dirs=[self.config])

    def write(self, name: str, text: str) -> None:
        (self.config / name).write_text(text, encoding="utf-8")

    def test_an_exec_bind_that_toggles_reads_as_on(self):
        self.assertEqual(
            self.state(binds({"dispatcher": "exec", "arg": "fcitx5-remote -t"})),
            hyprland.ON,
        )

    def test_the_modifier_and_key_are_not_what_is_matched(self):
        # Bound to Super+Space rather than a Shift tap, and just as working.
        self.assertEqual(
            self.state(binds({
                "modmask": 64, "key": "space",
                "dispatcher": "exec", "arg": "fcitx5-remote -t",
            })),
            hyprland.ON,
        )

    def test_it_asks_for_the_binds_hyprland_has_loaded(self):
        runner = FakeHyprctl(binds=binds())
        hyprland.toggle(runner, config_dirs=[self.config])
        self.assertEqual(runner.commands[0][:3], ["hyprctl", "binds", "-j"])

    def test_no_bind_and_no_configuration_reads_as_off(self):
        self.assertEqual(
            self.state(binds({"dispatcher": "exec", "arg": "kitty"})), hyprland.OFF
        )

    def test_another_fcitx5_remote_call_is_not_the_toggle(self):
        # `-s` switches input method and `-o` opens it; neither toggles.
        self.assertEqual(
            self.state(binds({"dispatcher": "exec", "arg": "fcitx5-remote -s pinyin"})),
            hyprland.OFF,
        )
        self.assertEqual(
            self.state(binds({"dispatcher": "exec", "arg": "fcitx5-remote -o"})),
            hyprland.OFF,
        )

    def test_a_missing_hyprctl_is_unknown(self):
        self.assertEqual(self.state(None), hyprland.UNKNOWN)

    def test_a_compositor_that_is_not_hyprland_is_unknown(self):
        self.assertEqual(self.state(NOT_HYPRLAND), hyprland.UNKNOWN)

    def test_output_that_is_not_json_is_unknown(self):
        self.assertEqual(self.state((0, "not json")), hyprland.UNKNOWN)

    def test_json_that_is_not_a_list_of_binds_is_unknown(self):
        self.assertEqual(self.state((0, '{"binds": []}')), hyprland.UNKNOWN)

    def test_a_bind_that_is_not_an_object_is_skipped_rather_than_fatal(self):
        self.assertEqual(self.state((0, '["nonsense", 3]')), hyprland.OFF)

    def test_an_unreachable_compositor_outranks_a_configuration_file(self):
        # A file saying the toggle is configured says nothing about whether it
        # is loaded. With no compositor to confirm it, the honest answer is
        # that we cannot tell.
        self.write("input.lua", 'hl.exec_cmd("fcitx5-remote -t")')
        self.assertEqual(self.state(None), hyprland.UNKNOWN)


class ToggleConfigurationScanTests(unittest.TestCase):
    """The fallback for a compositor whose binds do not carry their command.

    Under Omarchy every bind is registered from Lua, so `hyprctl binds -j`
    reports each one with the dispatcher `__lua` and an opaque index for its
    argument - the command is nowhere in the answer. And the left-Shift tap this
    plugin is looking for is not a bind at all: it is an `hl.on` keyboard
    handler, which `hyprctl binds` never lists. Detection that stopped at the
    binds would report "not on" on every Omarchy machine that has it.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.config = Path(self._tmp.name) / "hypr"
        self.config.mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name: str, text: str) -> Path:
        path = self.config / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def state(self) -> str:
        # Binds that hide their command: exactly what Omarchy produces.
        answer = binds({"dispatcher": "__lua", "arg": "6"})
        return hyprland.toggle(FakeHyprctl(binds=answer), config_dirs=[self.config])

    def test_a_lua_handler_the_binds_cannot_show_is_found_in_the_configuration(self):
        self.write("input.lua", 'hl.on("input.keyboard.key", function()\n'
                                '  hl.exec_cmd("fcitx5-remote -t")\n'
                                'end)\n')
        self.assertEqual(self.state(), hyprland.ON)

    def test_a_plain_hyprland_conf_bind_is_found_too(self):
        self.write("keybinds.conf", "bind = SUPER, space, exec, fcitx5-remote -t\n")
        self.assertEqual(self.state(), hyprland.ON)

    def test_it_looks_below_the_top_level(self):
        self.write("bindings/ime.lua", 'hl.exec_cmd("fcitx5-remote -t")\n')
        self.assertEqual(self.state(), hyprland.ON)

    def test_a_commented_out_line_does_not_count(self):
        self.write("input.lua", '-- hl.exec_cmd("fcitx5-remote -t")\n')
        self.assertEqual(self.state(), hyprland.OFF)
        self.write("input.lua", "# bind = SUPER, space, exec, fcitx5-remote -t\n")
        self.assertEqual(self.state(), hyprland.OFF)

    def test_a_backup_file_is_not_configuration(self):
        # `~/.config/hypr/hyprland.lua.bak.1787290304` is a real thing on a real
        # machine, and what it says is what the configuration used to say.
        self.write("hyprland.lua.bak.1787290304", 'hl.exec_cmd("fcitx5-remote -t")\n')
        self.assertEqual(self.state(), hyprland.OFF)

    def test_a_directory_that_is_not_there_is_not_an_error(self):
        answer = binds({"dispatcher": "__lua", "arg": "6"})
        self.assertEqual(
            hyprland.toggle(FakeHyprctl(binds=answer),
                            config_dirs=[self.config / "nowhere"]),
            hyprland.OFF,
        )

    def test_a_file_that_cannot_be_decoded_is_skipped_rather_than_fatal(self):
        (self.config / "binary.conf").write_bytes(b"\xff\xfe\x00binary")
        self.assertEqual(self.state(), hyprland.OFF)


class ReportTests(unittest.TestCase):
    """Both answers together, which is what `omarchy.ime status` carries."""

    def test_it_reports_both_pieces(self):
        runner = FakeHyprctl(getoption=BLUR_ON,
                             binds=binds({"dispatcher": "exec",
                                          "arg": "fcitx5-remote -t"}))
        self.assertEqual(
            hyprland.report(runner, config_dirs=[]),
            {"blur": hyprland.ON, "toggle": hyprland.ON},
        )

    def test_without_hyprland_both_are_unknown_and_neither_is_an_error(self):
        self.assertEqual(
            hyprland.report(FakeHyprctl(), config_dirs=[]),
            {"blur": hyprland.UNKNOWN, "toggle": hyprland.UNKNOWN},
        )

    def test_the_three_states_are_the_only_ones(self):
        self.assertEqual(
            sorted({hyprland.ON, hyprland.OFF, hyprland.UNKNOWN}),
            ["off", "on", "unknown"],
        )


class DefaultConfigDirTests(unittest.TestCase):
    """Where the fallback scan looks when nobody says."""

    def test_it_looks_in_the_users_own_hyprland_directory(self):
        directories = hyprland.default_config_dirs({"XDG_CONFIG_HOME": "/c",
                                                    "XDG_STATE_HOME": "/s"})
        self.assertIn(Path("/c/hypr"), directories)

    def test_it_looks_in_omarchys_drop_in_directory(self):
        # `~/.local/state/omarchy/toggles/hypr` is the one place Omarchy offers
        # for generated Hyprland configuration, so a toggle written there is
        # every bit as much in effect as one in ~/.config/hypr.
        directories = hyprland.default_config_dirs({"XDG_CONFIG_HOME": "/c",
                                                    "XDG_STATE_HOME": "/s"})
        self.assertIn(Path("/s/omarchy/toggles/hypr"), directories)


if __name__ == "__main__":
    unittest.main()
