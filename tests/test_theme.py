"""The candidate window's theme, generated from the active Omarchy theme.

Nothing here reads ~/.local/state/omarchy or runs hyprctl. Every palette is a
literal and every reader is injected, so the suite says the same thing on a
headless machine as it does on this desk.
"""
import configparser
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib import theme

ROOT = Path(__file__).resolve().parent.parent

# The active Omarchy theme on the machine this was written for: warm brown with
# a coral accent. Used as the known palette throughout.
RISTRETTO = {
    "mode": "dark",
    "accent": "#f38d70",
    "selection": "#403e41",
    "muted": "#72696a",
    "background": "#2c2525",
    "dark_background": "#211b1b",
    "lighter_background": "#3d2f2a",
    "foreground": "#e6d9db",
}

# The same theme in the other mode, so a rendering test can switch modes without
# switching anything else. Every installed Omarchy theme declares its mode.
LIGHT_PALETTE = dict(
    RISTRETTO, mode="light", background="#faf4ed", dark_background="#f2e9e1",
    lighter_background="#fffaf3", foreground="#575279",
)

# The user's own background images. Absolute, because that is the only kind of
# path Fcitx5 will open from outside the theme's own directory.
LIGHT_IMAGE = "/home/someone/Pictures/paper.png"
DARK_IMAGE = "/home/someone/Pictures/slate.jpg"
BOTH = {"backgroundImageLight": LIGHT_IMAGE, "backgroundImageDark": DARK_IMAGE}

# Every section the third-party theme this replaced had. The generated theme has
# to be a superset: a section that quietly disappears is a part of the window
# that quietly goes back to Fcitx5's own grey.
SHIPPED_SECTIONS = (
    "Metadata",
    "InputPanel",
    "InputPanel/TextMargin",
    "InputPanel/Background",
    "InputPanel/Background/Margin",
    "InputPanel/Highlight",
    "InputPanel/Highlight/Margin",
    "Menu",
    "Menu/Background",
    "Menu/Background/Margin",
    "Menu/ContentMargin",
    "Menu/Highlight",
    "Menu/Highlight/Margin",
    "Menu/Separator",
    "Menu/CheckBox",
    "Menu/SubMenu",
    "Menu/TextMargin",
)


def parse(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    # Fcitx5 keys are case sensitive; configparser lowercases them by default.
    parser.optionxform = str
    parser.read_string(text)
    return parser


def conf(palette=None, radius=17, source="ristretto", background=""):
    if palette is None:
        palette = RISTRETTO
    return parse(
        theme.render(palette, radius, source, background=background)["theme.conf"]
    )


def svg_attribute(text: str, name: str) -> str:
    match = re.search(rf'\b{name}="([^"]*)"', text)
    assert match, f"{name} missing from {text!r}"
    return match.group(1)


class RenderedFilesTests(unittest.TestCase):
    def test_a_render_is_a_complete_theme_directory(self):
        rendered = theme.render(RISTRETTO, 17, "ristretto")
        self.assertEqual(
            sorted(rendered),
            ["arrow.svg", "highlight.svg", "panel.svg", "radio.svg", "theme.conf"],
        )

    def test_every_rendered_file_is_text(self):
        for name, text in theme.render(RISTRETTO, 17, "ristretto").items():
            with self.subTest(name=name):
                self.assertIsInstance(text, str)
                self.assertTrue(text.strip())

    def test_rendering_is_pure(self):
        first = theme.render(RISTRETTO, 17, "ristretto")
        self.assertEqual(first, theme.render(RISTRETTO, 17, "ristretto"))

    def test_no_image_the_theme_names_is_missing(self):
        rendered = theme.render(RISTRETTO, 17, "ristretto")
        parser = parse(rendered["theme.conf"])
        for section in parser.sections():
            image = parser[section].get("Image")
            if image:
                with self.subTest(section=section):
                    # Every image a generated theme names by itself is a file it
                    # also renders. The user's own background is the one image
                    # named by absolute path, and it is not rendered here.
                    self.assertIn(image, rendered)


class ThemeConfStructureTests(unittest.TestCase):
    def test_it_parses_as_ini(self):
        self.assertTrue(conf().sections())

    def test_it_keeps_every_section_the_shipped_theme_had(self):
        sections = set(conf().sections())
        for section in SHIPPED_SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, sections)

    def test_the_content_margin_that_makes_the_radii_concentric_is_declared(self):
        self.assertIn("InputPanel/ContentMargin", conf().sections())


class ContentInsetTests(unittest.TestCase):
    """The gap between the window's edge and the highlight inside it.

    Concentric rather than merely nested is a measurement: two rounded
    rectangles share a centre of curvature exactly when the gap between them
    equals the difference of their radii. A fixed 6 was wrong for every rounded
    theme and wrong by more the rounder it got - at Hyprland's default 17 the
    answer is 9, so the highlighted first candidate was crowded three pixels
    into the window's corner.
    """

    def test_the_inset_is_the_difference_of_the_two_radii(self):
        for radius in (12, 17, 20, 24, 40):
            with self.subTest(radius=radius):
                self.assertEqual(
                    theme.content_inset(radius),
                    theme.panel_radius(radius) - theme.highlight_radius(radius),
                )

    def test_the_default_hyprland_rounding_gives_nine_not_six(self):
        self.assertEqual(theme.content_inset(17), 9)

    def test_a_square_theme_still_has_breathing_room(self):
        # Both radii are zero there, so the difference is zero and the highlight
        # would sit flush against the window's edge.
        self.assertEqual(theme.content_inset(0), theme.MIN_CONTENT_INSET)

    def test_a_rounder_theme_never_gets_a_smaller_inset(self):
        insets = [theme.content_inset(radius) for radius in range(0, 64)]
        self.assertEqual(insets, sorted(insets))

    def test_it_reaches_both_content_margins(self):
        for radius, expected in ((0, theme.MIN_CONTENT_INSET), (17, 9), (40, 20)):
            parser = conf(radius=radius)
            for section in ("InputPanel/ContentMargin", "Menu/ContentMargin"):
                with self.subTest(radius=radius, section=section):
                    for side in ("Left", "Right", "Top", "Bottom"):
                        self.assertEqual(int(parser[section][side]), expected)

    def test_the_menu_takes_the_same_answer_as_the_candidate_window(self):
        # Same outer radius, same inner one, so the same geometry. Only the text
        # margins differ, because the menu is a denser list.
        parser = conf()
        self.assertEqual(dict(parser["Menu/ContentMargin"]),
                         dict(parser["InputPanel/ContentMargin"]))
        self.assertNotEqual(dict(parser["Menu/TextMargin"]),
                            dict(parser["InputPanel/TextMargin"]))

    def test_the_metadata_names_the_omarchy_theme_it_came_from(self):
        metadata = conf()["Metadata"]
        self.assertIn("ristretto", metadata["Name"])
        self.assertEqual(metadata["ScaleWithDPI"], "True")

    def test_the_metadata_survives_an_unknown_source(self):
        metadata = conf(source="")["Metadata"]
        self.assertTrue(metadata["Name"].strip())


class ColourMappingTests(unittest.TestCase):
    """Each colour is the Omarchy role that means the same thing on screen."""

    def setUp(self):
        self.conf = conf()

    def test_an_unselected_candidate_uses_the_theme_foreground(self):
        self.assertEqual(self.conf["InputPanel"]["NormalColor"], "#e6d9db")

    def test_the_window_floats_above_the_page_rather_than_sinking_into_it(self):
        # The window used to be painted in `background`, which on the theme the
        # owner was running made it byte-identical to the terminal underneath -
        # a popup with no edge at all. A dark theme's popup takes the rung above
        # the page.
        surface = self.conf["InputPanel/Background"]["Color"]
        self.assertEqual(surface, RISTRETTO["lighter_background"])
        self.assertNotEqual(surface, RISTRETTO["background"])

    def test_a_light_theme_takes_the_rung_below_the_page_instead(self):
        surface = conf(LIGHT_PALETTE)["InputPanel/Background"]["Color"]
        self.assertEqual(surface, LIGHT_PALETTE["dark_background"])
        self.assertNotEqual(surface, LIGHT_PALETTE["background"])

    def test_the_window_carries_an_edge_drawn_from_the_palette(self):
        # A popup floats over an arbitrary backdrop - a photo, a video, a light
        # web page - and no fill differs from all of them. The edge is what
        # makes it a window.
        border = self.conf["InputPanel/Background"]["BorderColor"]
        self.assertNotEqual(border, self.conf["InputPanel/Background"]["Color"])
        self.assertGreater(
            theme.contrast(border, self.conf["InputPanel/Background"]["Color"]), 1.2
        )

    def test_the_selected_candidate_sits_on_the_accent(self):
        self.assertEqual(self.conf["InputPanel/Highlight"]["Color"], "#f38d70")

    def test_the_selected_candidate_text_is_the_readable_one_over_the_accent(self):
        # dark_background over the coral accent is 7.2:1; foreground is 1.7:1.
        self.assertEqual(self.conf["InputPanel"]["HighlightCandidateColor"], "#211b1b")

    def test_a_dark_accent_flips_the_selected_candidate_text_to_the_light_role(self):
        dark_accent = dict(RISTRETTO, accent="#1b2f4a")
        self.assertEqual(
            conf(dark_accent)["InputPanel"]["HighlightCandidateColor"], "#e6d9db"
        )

    def test_the_preedit_highlight_is_the_accent_on_the_selection_role(self):
        self.assertEqual(self.conf["InputPanel"]["HighlightColor"], "#f38d70")
        self.assertEqual(self.conf["InputPanel"]["HighlightBackgroundColor"], "#403e41")

    def test_a_candidate_annotation_is_muted(self):
        self.assertEqual(self.conf["InputPanel"]["CandidateCommentColor"], "#72696a")

    def test_the_annotation_on_the_selected_candidate_stays_secondary(self):
        panel = self.conf["InputPanel"]
        self.assertEqual(panel["HighlightCandidateLabelColor"], "#211b1b")
        # 35% of the way from the selected text colour back towards the accent:
        # still legible, still visibly the quieter of the two.
        self.assertEqual(panel["HighlightCandidateCommentColor"], "#6b4339")

    def test_the_menu_follows_the_same_roles(self):
        # Same kind of thing: a popup over arbitrary content, same outer radius,
        # same inner one. Only its text margins differ.
        self.assertEqual(self.conf["Menu"]["NormalColor"], "#e6d9db")
        self.assertEqual(self.conf["Menu"]["HighlightCandidateColor"], "#211b1b")
        self.assertEqual(
            self.conf["Menu/Background"]["Color"],
            self.conf["InputPanel/Background"]["Color"],
        )
        self.assertEqual(
            self.conf["Menu/Background"]["BorderColor"],
            self.conf["InputPanel/Background"]["BorderColor"],
        )
        self.assertEqual(self.conf["Menu/Highlight"]["Color"], "#f38d70")
        self.assertEqual(self.conf["Menu/Separator"]["Color"], "#72696a")

    def test_no_colour_of_the_theme_it_replaced_survives(self):
        rendered = theme.render(RISTRETTO, 17, "ristretto")
        for stale in ("#cad3f5", "#24273a", "#ee99a0", "#363a4f", "#f5bde6"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, "".join(rendered.values()))

    def test_every_colour_written_comes_from_the_palette(self):
        # Two blends, both of palette colours: #6b4339 is the comment on the
        # selected candidate, #786b68 is the window's edge, mixed from its own
        # surface towards the foreground.
        allowed = set(RISTRETTO.values()) | {"#6b4339", "#786b68"}
        found = set(re.findall(r"#[0-9a-f]{6}", "".join(
            theme.render(RISTRETTO, 17, "ristretto").values()
        )))
        self.assertTrue(found <= allowed, found - allowed)


class PaletteFallbackTests(unittest.TestCase):
    """A palette is whatever the theme's colors.toml happens to carry."""

    def test_a_missing_selection_falls_back_to_the_lighter_background(self):
        without = {k: v for k, v in RISTRETTO.items() if k != "selection"}
        self.assertEqual(
            conf(without)["InputPanel"]["HighlightBackgroundColor"], "#3d2f2a"
        )

    def test_a_missing_lighter_background_falls_back_to_the_background(self):
        without = {
            k: v for k, v in RISTRETTO.items()
            if k not in ("selection", "lighter_background")
        }
        self.assertEqual(
            conf(without)["InputPanel"]["HighlightBackgroundColor"], "#2c2525"
        )

    def test_a_missing_muted_falls_back_to_the_foreground(self):
        without = {k: v for k, v in RISTRETTO.items() if k != "muted"}
        self.assertEqual(conf(without)["InputPanel"]["CandidateCommentColor"], "#e6d9db")

    def test_a_missing_dark_background_falls_back_to_the_background(self):
        without = {k: v for k, v in RISTRETTO.items() if k != "dark_background"}
        self.assertEqual(
            conf(without)["InputPanel"]["HighlightCandidateColor"], "#2c2525"
        )

    def test_a_missing_accent_falls_back_to_the_foreground(self):
        without = {k: v for k, v in RISTRETTO.items() if k != "accent"}
        self.assertEqual(conf(without)["InputPanel/Highlight"]["Color"], "#e6d9db")

    def test_an_empty_palette_still_renders_a_complete_theme(self):
        parser = conf({})
        for section in SHIPPED_SECTIONS:
            self.assertIn(section, parser.sections())
        self.assertEqual(
            parser["InputPanel"]["NormalColor"], theme.FALLBACK_PALETTE["foreground"]
        )

    def test_a_malformed_colour_is_treated_as_absent(self):
        broken = dict(RISTRETTO, muted="not a colour")
        self.assertEqual(conf(broken)["InputPanel"]["CandidateCommentColor"], "#e6d9db")

    def test_colours_are_normalised_to_lowercase_six_digit_hex(self):
        shouty = dict(RISTRETTO, foreground="#E6D9DB")
        self.assertEqual(conf(shouty)["InputPanel"]["NormalColor"], "#e6d9db")


class CornerRadiusTests(unittest.TestCase):
    """Flat colours give square corners; the rounding lives in the nine-slices."""

    def test_the_hyprland_radius_reaches_the_panel_svg(self):
        panel = theme.render(RISTRETTO, 17, "ristretto")["panel.svg"]
        self.assertEqual(svg_attribute(panel, "rx"), "17")
        self.assertEqual(svg_attribute(panel, "ry"), "17")

    def test_the_highlight_gets_the_inner_radius_omarchy_uses_for_nested_shapes(self):
        # Omarchy's own kit nests with Math.max(2, Style.cornerRadius / 2).
        highlight = theme.render(RISTRETTO, 17, "ristretto")["highlight.svg"]
        self.assertEqual(svg_attribute(highlight, "rx"), "8")

    def test_radius_zero_produces_square_corners(self):
        rendered = theme.render(RISTRETTO, 0, "ristretto")
        for name in ("panel.svg", "highlight.svg"):
            with self.subTest(name=name):
                self.assertEqual(svg_attribute(rendered[name], "rx"), "0")
                self.assertEqual(svg_attribute(rendered[name], "ry"), "0")

    def test_a_negative_radius_is_clamped_rather_than_drawn(self):
        panel = theme.render(RISTRETTO, -4, "ristretto")["panel.svg"]
        self.assertEqual(svg_attribute(panel, "rx"), "0")

    def test_the_backgrounds_are_drawn_from_the_svgs(self):
        parser = conf()
        self.assertEqual(parser["InputPanel/Background"]["Image"], "panel.svg")
        self.assertEqual(parser["InputPanel/Highlight"]["Image"], "highlight.svg")
        self.assertEqual(parser["Menu/Background"]["Image"], "panel.svg")
        self.assertEqual(parser["Menu/Highlight"]["Image"], "highlight.svg")

    def test_the_svg_carries_the_colour_of_the_surface_it_backs(self):
        rendered = theme.render(RISTRETTO, 17, "ristretto")
        self.assertEqual(svg_attribute(rendered["panel.svg"], "fill"),
                         theme.surface(RISTRETTO))
        self.assertEqual(svg_attribute(rendered["highlight.svg"], "fill"), "#f38d70")

    def test_the_window_edge_is_drawn_into_the_panel_svg(self):
        # classicui only paints BorderColor/BorderWidth on the blank surface it
        # builds when no Image loads, and this theme always names one - so the
        # edge that is actually seen has to be in the image, the same way the
        # rounding is.
        panel = theme.render(RISTRETTO, 17, "ristretto")["panel.svg"]
        self.assertIn(f'stroke="{theme.surface_border(RISTRETTO)}"', panel)
        self.assertIn(f'stroke-width="{theme.BORDER_WIDTH}"', panel)

    def test_the_highlight_has_no_edge_of_its_own(self):
        # It is inside the window, not floating over anything.
        self.assertNotIn(
            "stroke", theme.render(RISTRETTO, 17, "ristretto")["highlight.svg"]
        )


class NineSliceGeometryTests(unittest.TestCase):
    """Fcitx5 slices the image by the Margin block and stretches the middle.

    Getting this wrong is visible: a slice that cuts through the arc smears a
    half-transparent pixel across the whole edge, and a slice narrower than the
    arc clips the corner off.
    """

    def dimensions(self, name, radius=17):
        svg = theme.render(RISTRETTO, radius, "ristretto")[name]
        return int(svg_attribute(svg, "width")), int(svg_attribute(svg, "height"))

    def margin(self, section, radius=17):
        block = conf(radius=radius)[section]
        return {side: int(block[side]) for side in ("Left", "Right", "Top", "Bottom")}

    def test_the_slice_clears_the_arc_on_every_side(self):
        for section, name in (
            ("InputPanel/Background/Margin", "panel.svg"),
            ("InputPanel/Highlight/Margin", "highlight.svg"),
            ("Menu/Background/Margin", "panel.svg"),
            ("Menu/Highlight/Margin", "highlight.svg"),
        ):
            svg = theme.render(RISTRETTO, 17, "ristretto")[name]
            radius = int(svg_attribute(svg, "rx"))
            for side, value in self.margin(section).items():
                with self.subTest(section=section, side=side):
                    self.assertGreater(value, radius)

    def test_the_stretched_middle_is_wide_enough_to_resample_cleanly(self):
        for name, section in (
            ("panel.svg", "InputPanel/Background/Margin"),
            ("highlight.svg", "InputPanel/Highlight/Margin"),
        ):
            width, height = self.dimensions(name)
            margin = self.margin(section)
            with self.subTest(name=name):
                self.assertGreaterEqual(width - margin["Left"] - margin["Right"], 2)
                self.assertGreaterEqual(height - margin["Top"] - margin["Bottom"], 2)

    def test_the_slices_stay_inside_the_image(self):
        for radius in (0, 1, 8, 17, 40):
            for name, section in (
                ("panel.svg", "InputPanel/Background/Margin"),
                ("highlight.svg", "InputPanel/Highlight/Margin"),
            ):
                width, height = self.dimensions(name, radius)
                margin = self.margin(section, radius)
                with self.subTest(radius=radius, name=name):
                    self.assertLess(margin["Left"] + margin["Right"], width)
                    self.assertLess(margin["Top"] + margin["Bottom"], height)

    def test_the_image_is_square_so_the_slice_is_the_same_on_both_axes(self):
        width, height = self.dimensions("panel.svg")
        self.assertEqual(width, height)

    def test_the_rect_fills_the_whole_image(self):
        svg = theme.render(RISTRETTO, 17, "ristretto")["panel.svg"]
        self.assertEqual(svg_attribute(svg, "width"), "46")
        self.assertEqual(
            (svg_attribute(svg, "x"), svg_attribute(svg, "y")), ("0", "0")
        )
        self.assertIn('viewBox="0 0 46 46"', svg)

    def test_the_declared_margin_is_the_one_the_svg_was_drawn_for(self):
        self.assertEqual(self.margin("InputPanel/Background/Margin"),
                         dict.fromkeys(("Left", "Right", "Top", "Bottom"), 19))
        self.assertEqual(self.margin("InputPanel/Highlight/Margin"),
                         dict.fromkeys(("Left", "Right", "Top", "Bottom"), 10))


class MenuImageTests(unittest.TestCase):
    """The menu's check mark and submenu arrow are generated too.

    They used to be third-party PNGs. Naming a file that is no longer installed
    would leave a checked menu entry with nothing to show for it.
    """

    def test_the_check_mark_is_drawn_in_the_accent(self):
        radio = theme.render(RISTRETTO, 17, "ristretto")["radio.svg"]
        self.assertEqual(svg_attribute(radio, "fill"), "#f38d70")
        self.assertEqual(conf()["Menu/CheckBox"]["Image"], "radio.svg")

    def test_the_submenu_arrow_is_drawn_in_the_foreground(self):
        arrow = theme.render(RISTRETTO, 17, "ristretto")["arrow.svg"]
        self.assertEqual(svg_attribute(arrow, "fill"), "#e6d9db")
        self.assertEqual(conf()["Menu/SubMenu"]["Image"], "arrow.svg")


class FcitxWiringTests(unittest.TestCase):
    def test_the_theme_directory_name_is_the_plugins_own(self):
        # Not the Omarchy theme's name: the directory must not churn when the
        # user switches themes, or every switch would strand the old one.
        self.assertEqual(theme.DIRECTORY, "omarchy-ime")
        self.assertNotIn("ristretto", theme.DIRECTORY)

    def test_the_generated_classicui_config_selects_the_generated_theme(self):
        from lib import generate, settings as settings_lib

        rendered = generate.render_fcitx5(settings_lib.resolve({}), theme.DIRECTORY)
        parser = configparser.ConfigParser()
        parser.optionxform = str
        # classicui.conf is a flat key=value file with no section header.
        parser.read_string("[classicui]\n" + rendered[generate.CLASSICUI_PATH])
        self.assertEqual(parser["classicui"]["Theme"], theme.DIRECTORY)
        self.assertEqual(parser["classicui"]["DarkTheme"], theme.DIRECTORY)

    def test_classicui_is_no_longer_shipped_as_a_fixed_payload_file(self):
        # It carries a key the settings decide, so it cannot be a file that
        # ships the same for everyone.
        self.assertFalse(
            (ROOT / "payload" / "fcitx5" / "conf" / "classicui.conf").exists()
        )

    def test_the_third_party_theme_is_no_longer_shipped(self):
        self.assertFalse((ROOT / "payload" / "themes").exists())


class PaletteReadingTests(unittest.TestCase):
    """Reading colors.toml, on a file this test wrote."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, text: str) -> Path:
        path = self.root / "colors.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_flat_palette_is_read_key_by_key(self):
        path = self.write('mode = "dark"\n\naccent = "#f38d70"\nmuted = "#72696a"\n')
        palette = theme.read_palette(path)
        self.assertEqual(palette["accent"], "#f38d70")
        self.assertEqual(palette["muted"], "#72696a")
        self.assertEqual(palette["mode"], "dark")

    def test_a_missing_file_reads_as_nothing(self):
        self.assertIsNone(theme.read_palette(self.root / "absent.toml"))

    def test_an_unparsable_file_reads_as_nothing(self):
        self.assertIsNone(theme.read_palette(self.write("this is not = = toml\n")))

    def test_a_name_is_read_and_stripped(self):
        path = self.root / "theme.name"
        path.write_text("ristretto\n", encoding="utf-8")
        self.assertEqual(theme.read_name(path), "ristretto")

    def test_a_missing_name_is_empty(self):
        self.assertEqual(theme.read_name(self.root / "absent"), "")


class FakeRunner:
    """Stands in for hyprctl. No test in this suite runs the real one."""

    def __init__(self, stdout="int: 17\nset: true\n", returncode=0, error=None):
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        if self.error is not None:
            raise self.error

        class Result:
            pass

        result = Result()
        result.returncode = self.returncode
        result.stdout = self.stdout
        result.stderr = ""
        return result


class RadiusReadingTests(unittest.TestCase):
    def test_the_int_line_is_the_answer(self):
        self.assertEqual(theme.read_radius(FakeRunner()), 17)

    def test_it_asks_hyprland_for_the_decoration_rounding(self):
        runner = FakeRunner()
        theme.read_radius(runner)
        self.assertEqual(runner.commands, [["hyprctl", "getoption", "decoration:rounding"]])

    def test_other_lines_are_ignored(self):
        runner = FakeRunner(stdout="option: decoration:rounding\nint: 4\nset: true\n")
        self.assertEqual(theme.read_radius(runner), 4)

    def test_a_missing_hyprctl_falls_back_to_square(self):
        runner = FakeRunner(error=FileNotFoundError("hyprctl"))
        self.assertEqual(theme.read_radius(runner), theme.FALLBACK_RADIUS)
        self.assertEqual(theme.FALLBACK_RADIUS, 0)

    def test_a_failed_call_falls_back_to_square(self):
        self.assertEqual(theme.read_radius(FakeRunner(returncode=1)), 0)

    def test_output_without_an_int_line_falls_back_to_square(self):
        self.assertEqual(theme.read_radius(FakeRunner(stdout="no such option\n")), 0)

    def test_a_nonsense_int_line_falls_back_to_square(self):
        self.assertEqual(theme.read_radius(FakeRunner(stdout="int: seventeen\n")), 0)

    def test_a_negative_radius_is_clamped(self):
        self.assertEqual(theme.read_radius(FakeRunner(stdout="int: -1\n")), 0)


class ResolveTests(unittest.TestCase):
    """The thin loader, with every real path injected."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.colors = self.root / "colors.toml"
        self.name = self.root / "theme.name"
        self.addCleanup(self._tmp.cleanup)

    def write_theme(self):
        self.colors.write_text(
            "".join(f'{k} = "{v}"\n' for k, v in RISTRETTO.items()), encoding="utf-8"
        )
        self.name.write_text("ristretto\n", encoding="utf-8")

    def resolve(self, **overrides):
        options = {
            "colors_path": self.colors,
            "name_path": self.name,
            "runner": FakeRunner(),
        }
        options.update(overrides)
        return theme.resolve(**options)

    def test_a_readable_theme_is_used(self):
        self.write_theme()
        result = self.resolve()
        self.assertFalse(result["fallback"])
        self.assertEqual(result["source"], "ristretto")
        self.assertEqual(result["radius"], 17)
        self.assertIn("#f38d70", result["files"]["theme.conf"])

    def test_an_unreadable_theme_falls_back_and_says_why(self):
        result = self.resolve()
        self.assertTrue(result["fallback"])
        self.assertTrue(result["reason"].strip())
        self.assertIn(
            theme.FALLBACK_PALETTE["foreground"], result["files"]["theme.conf"]
        )

    def test_the_fallback_still_renders_a_complete_theme(self):
        rendered = self.resolve()["files"]
        self.assertEqual(
            sorted(rendered),
            ["arrow.svg", "highlight.svg", "panel.svg", "radio.svg", "theme.conf"],
        )

    def test_the_fallback_palette_is_legible(self):
        parser = parse(theme.render(theme.FALLBACK_PALETTE, 0, "")["theme.conf"])
        panel = parser["InputPanel"]
        self.assertNotEqual(panel["NormalColor"], parser["InputPanel/Background"]["Color"])
        self.assertGreater(
            theme.contrast(panel["NormalColor"], parser["InputPanel/Background"]["Color"]),
            7.0,
        )
        self.assertGreater(
            theme.contrast(
                panel["HighlightCandidateColor"], parser["InputPanel/Highlight"]["Color"]
            ),
            4.5,
        )

    def test_a_theme_without_a_name_still_deploys(self):
        self.colors.write_text('accent = "#f38d70"\n', encoding="utf-8")
        result = self.resolve()
        self.assertFalse(result["fallback"])
        self.assertEqual(result["source"], "")

    def test_a_missing_hyprland_does_not_stop_the_theme(self):
        self.write_theme()
        result = self.resolve(runner=FakeRunner(error=FileNotFoundError("hyprctl")))
        self.assertFalse(result["fallback"])
        self.assertEqual(result["radius"], 0)
        self.assertEqual(svg_attribute(result["files"]["panel.svg"], "rx"), "0")

    def test_resolved_builds_the_same_answer_from_a_palette_in_hand(self):
        made = theme.resolved(RISTRETTO, 17, "ristretto")
        self.write_theme()
        self.assertEqual(made["files"], self.resolve()["files"])

    def test_the_settings_it_is_handed_choose_the_background_by_mode(self):
        self.write_theme()
        result = self.resolve(settings=BOTH)
        self.assertEqual(result["background"], DARK_IMAGE)
        self.assertIn(f"Image={DARK_IMAGE}", result["files"]["theme.conf"])

    def test_a_light_theme_on_the_same_settings_takes_the_other_image(self):
        self.colors.write_text(
            "".join(f'{k} = "{v}"\n' for k, v in LIGHT_PALETTE.items()),
            encoding="utf-8",
        )
        result = self.resolve(settings=BOTH)
        self.assertEqual(result["background"], LIGHT_IMAGE)
        self.assertIn(f"Image={LIGHT_IMAGE}", result["files"]["theme.conf"])

    def test_no_settings_means_no_background_and_the_generated_panel(self):
        self.write_theme()
        result = self.resolve()
        self.assertEqual(result["background"], "")
        self.assertIn("Image=panel.svg", result["files"]["theme.conf"])

    def test_the_fallback_palette_is_dark_so_it_takes_the_dark_image(self):
        result = self.resolve(settings=BOTH)
        self.assertTrue(result["fallback"])
        self.assertEqual(result["background"], DARK_IMAGE)


class ModeTests(unittest.TestCase):
    """Every Omarchy theme declares `mode = "dark"` or `mode = "light"`."""

    def test_a_dark_theme_reads_as_dark(self):
        self.assertEqual(theme.mode(RISTRETTO), "dark")

    def test_a_light_theme_reads_as_light(self):
        self.assertEqual(theme.mode(LIGHT_PALETTE), "light")

    def test_a_palette_that_does_not_say_is_treated_as_dark(self):
        # The fallback palette is dark, so an unlabelled theme lands where the
        # colours the plugin would otherwise draw already are.
        self.assertEqual(theme.mode({k: v for k, v in RISTRETTO.items()
                                     if k != "mode"}), "dark")

    def test_a_nonsense_mode_is_treated_as_dark_rather_than_raising(self):
        self.assertEqual(theme.mode(dict(RISTRETTO, mode="beige")), "dark")

    def test_the_case_and_spacing_a_hand_edit_leaves_do_not_matter(self):
        self.assertEqual(theme.mode(dict(RISTRETTO, mode=" Light ")), "light")


class BackgroundChoiceTests(unittest.TestCase):
    """Which of the two images a theme gets is the theme's own mode."""

    def test_a_dark_theme_takes_the_dark_image(self):
        self.assertEqual(theme.background_for(RISTRETTO, BOTH), DARK_IMAGE)

    def test_a_light_theme_takes_the_light_image(self):
        self.assertEqual(theme.background_for(LIGHT_PALETTE, BOTH), LIGHT_IMAGE)

    def test_a_mode_with_no_image_set_gets_none_even_when_the_other_has_one(self):
        only_light = {"backgroundImageLight": LIGHT_IMAGE, "backgroundImageDark": ""}
        self.assertEqual(theme.background_for(RISTRETTO, only_light), "")
        self.assertEqual(theme.background_for(LIGHT_PALETTE, only_light), LIGHT_IMAGE)

    def test_no_settings_at_all_means_no_image(self):
        self.assertEqual(theme.background_for(RISTRETTO, None), "")
        self.assertEqual(theme.background_for(RISTRETTO, {}), "")

    def test_a_hand_edited_non_string_means_no_image(self):
        self.assertEqual(theme.background_for(RISTRETTO, {"backgroundImageDark": 5}), "")


class BackgroundImageRenderTests(unittest.TestCase):
    """The user's own image behind the candidates, when they have set one.

    classicui's ThemeImage builds the background from `Image` when it loads and
    from `Color` only when it does not - the two are alternatives, not layers -
    so the image replaces the generated panel.svg rather than sitting on top of
    the theme colour. `Color` is still written, because that is exactly what
    classicui falls back to if the file is moved or deleted after a deploy.
    """

    def test_the_image_is_named_where_the_generated_panel_would_be(self):
        rendered = conf(background=DARK_IMAGE)
        self.assertEqual(rendered["InputPanel/Background"]["Image"], DARK_IMAGE)

    def test_the_theme_colour_stays_as_the_fallback_for_a_file_that_will_not_load(self):
        rendered = conf(background=DARK_IMAGE)
        self.assertEqual(rendered["InputPanel/Background"]["Color"],
                         theme.surface(RISTRETTO))

    def test_without_an_image_the_generated_panel_is_used(self):
        self.assertEqual(conf()["InputPanel/Background"]["Image"], "panel.svg")

    def test_the_menu_keeps_the_generated_panel(self):
        # The setting is the candidate window's, and Fcitx5's right-click menu
        # is a different window that has no business wearing someone's photo.
        self.assertEqual(conf(background=DARK_IMAGE)["Menu/Background"]["Image"],
                         "panel.svg")

    def test_the_highlight_is_untouched(self):
        self.assertEqual(conf(background=DARK_IMAGE)["InputPanel/Highlight"]["Image"],
                         "highlight.svg")

    def test_the_nine_slice_margin_is_still_declared(self):
        rendered = conf(background=DARK_IMAGE)
        self.assertEqual(rendered["InputPanel/Background/Margin"]["Left"], "19")

    def test_the_files_beside_theme_conf_do_not_change(self):
        with_image = theme.render(RISTRETTO, 17, "ristretto", background=DARK_IMAGE)
        without = theme.render(RISTRETTO, 17, "ristretto")
        self.assertEqual(sorted(with_image), sorted(without))
        for name in without:
            if name != "theme.conf":
                with self.subTest(name=name):
                    self.assertEqual(with_image[name], without[name])

    def test_nothing_is_copied_into_the_theme_directory(self):
        # Fcitx5 opens an absolute Image path directly, so the file stays where
        # the user put it and the theme directory holds only generated text.
        rendered = theme.render(RISTRETTO, 17, "ristretto", background=DARK_IMAGE)
        self.assertEqual(
            sorted(rendered),
            ["arrow.svg", "highlight.svg", "panel.svg", "radio.svg", "theme.conf"],
        )
        for text in rendered.values():
            self.assertIsInstance(text, str)

    def test_rendering_with_an_image_is_still_pure(self):
        first = theme.render(RISTRETTO, 17, "ristretto", background=DARK_IMAGE)
        self.assertEqual(
            first, theme.render(RISTRETTO, 17, "ristretto", background=DARK_IMAGE)
        )


class BackgroundImageCombinationTests(unittest.TestCase):
    """Every mode against every combination of set and unset paths."""

    CASES = (
        ("dark", RISTRETTO, "", "", ""),
        ("dark", RISTRETTO, LIGHT_IMAGE, "", ""),
        ("dark", RISTRETTO, "", DARK_IMAGE, DARK_IMAGE),
        ("dark", RISTRETTO, LIGHT_IMAGE, DARK_IMAGE, DARK_IMAGE),
        ("light", LIGHT_PALETTE, "", "", ""),
        ("light", LIGHT_PALETTE, LIGHT_IMAGE, "", LIGHT_IMAGE),
        ("light", LIGHT_PALETTE, "", DARK_IMAGE, ""),
        ("light", LIGHT_PALETTE, LIGHT_IMAGE, DARK_IMAGE, LIGHT_IMAGE),
    )

    def test_each_mode_takes_its_own_image_or_the_generated_panel(self):
        for mode, palette, light, dark, expected in self.CASES:
            with self.subTest(mode=mode, light=bool(light), dark=bool(dark)):
                chosen = theme.background_for(palette, {
                    "backgroundImageLight": light, "backgroundImageDark": dark,
                })
                self.assertEqual(chosen, expected)
                rendered = parse(
                    theme.render(palette, 17, "t", background=chosen)["theme.conf"]
                )
                self.assertEqual(
                    rendered["InputPanel/Background"]["Image"],
                    expected or "panel.svg",
                )


class ContrastTests(unittest.TestCase):
    """The one piece of colour maths the mapping leans on."""

    def test_black_on_white_is_the_maximum(self):
        self.assertAlmostEqual(theme.contrast("#000000", "#ffffff"), 21.0, places=2)

    def test_a_colour_against_itself_is_the_minimum(self):
        self.assertAlmostEqual(theme.contrast("#f38d70", "#f38d70"), 1.0, places=6)

    def test_it_does_not_care_which_way_round_the_pair_is(self):
        self.assertAlmostEqual(
            theme.contrast("#211b1b", "#f38d70"), theme.contrast("#f38d70", "#211b1b")
        )


if __name__ == "__main__":
    unittest.main()
