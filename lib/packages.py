# lib/packages.py
"""Detect and install the packages the input method needs.

This is the one privileged thing the plugin does, so it is kept behind an
explicit call: nothing here runs as part of a deploy. Omarchy's own package
commands are used rather than pacman, so the plugin inherits whatever policy
the system has for AUR access and confirmation.

Two things this module has to get right, both learned the hard way.

Asking is per package. `omarchy pkg present` exits non-zero when *any* of the
names it is handed is absent, so one call for a whole group can only answer
"something here is missing" - and reporting the group back named packages the
user could see were installed. One call per package is what makes the answer
per package.

Installing needs a terminal. `omarchy pkg add` declares
`omarchy:requires-sudo=true` and shells out to `sudo pacman -S`, which asks for
a password on a stock Arch system - and a Quickshell `Process` has no tty for
that prompt to appear on. Started from the panel it could only ever fail, and
with its output captured and dropped it failed silently. So the panel's install
is handed to a floating terminal the user can see and type a password into,
which is exactly how Omarchy's own `omarchy install app` does it.

This plugin defines no privilege policy of its own. It writes nothing under
/etc, grants itself no exemption from that password prompt, and asks for
elevation only by calling the same Omarchy command a user would type.
"""
from __future__ import annotations

import shlex
import subprocess

# noto-fonts-cjk is here because lib/generate names its face in every user's
# classicui.conf. It is an Omarchy base package, so on Omarchy this line asks
# for something that is already there - which is the point: the font the
# candidate window is told to draw CJK with has to be a font the machine has.
REQUIRED = ["fcitx5", "fcitx5-configtool", "fcitx5-rime", "noto-fonts-cjk"]
AUR = ["rime-ice-git"]

# Nothing here may hang the panel: `missing()` is on a three-second poll.
COMMAND_TIMEOUT = 30

# Omarchy's own way of running something privileged from a graphical context.
# It launches the user's terminal with the Omarchy presentation wrapper around
# the command, so the sudo prompt lands somewhere a person can answer it.
TERMINAL_LAUNCHER = "omarchy-launch-floating-terminal-with-presentation"

# What "the command could not be run at all" looks like, as distinct from a
# command that ran and answered no. None rather than a number, because every
# number is an exit status something really returns - 127 included.
UNAVAILABLE = None


def _run(command: list[str]) -> tuple[int | None, str, str]:
    """Run a command and return (returncode, stdout, stderr).

    Guarded and bounded like lib/imestate._run and lib/theme.read_radius: an
    absent `omarchy`, or one that hangs, is a report this plugin cannot make -
    never a traceback out of `ime-manager status --json`, which the panel polls
    forever.

    stderr is carried out rather than dropped. It is the only thing that can
    tell the user why an install failed, and dropping it is what left the panel
    printing a bare "Package installation failed".
    """
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return UNAVAILABLE, "", f"{command[0]}: {error}"
    return result.returncode, result.stdout, result.stderr


def _run_attached(command: list[str]) -> int | None:
    """Run a command with this process's own terminal.

    Nothing is captured, on purpose: this is the path taken when `ime-manager`
    is being run by a person in a terminal, and sudo can only ask for a
    password down a tty it can still see.
    """
    try:
        return subprocess.run(list(command)).returncode
    except OSError:
        return UNAVAILABLE


def check(runner=_run) -> dict:
    """Which required packages are absent, and whether the question got asked.

    Returns `{"missing", "error"}`. A package whose query could not be run is
    not reported missing: "we could not ask" and "it is not installed" are
    different answers, and only one of them is fixed by installing something.
    """
    absent: list[str] = []
    problems: list[str] = []
    for name in REQUIRED + AUR:
        code, _, stderr = runner(["omarchy", "pkg", "present", name])
        if code is UNAVAILABLE:
            problems.append((stderr or "").strip() or f"could not ask about {name}")
        elif code != 0:
            absent.append(name)
    return {"missing": absent, "error": problems[0] if problems else ""}


def missing(runner=_run) -> list[str]:
    """Return the packages that are not installed."""
    return check(runner)["missing"]


def install_script(names: list[str]) -> str:
    """The shell one-liner a terminal is asked to run.

    Official and AUR packages go to their own Omarchy wrapper, and the AUR is
    checked before it is used so an unreachable AUR is a sentence rather than a
    yay error. The steps are chained with `&&`: once pacman has refused there
    is nothing to be gained by going on to the AUR.

    The AUR check is wrapped in a brace group, and that is not decoration. `&&`
    and `||` have equal precedence and associate left to right, so a bare
    `add && accessible || { fail; } && aur-add` means "if add *or* accessible
    fails, print the AUR message" and then runs the AUR install anyway when
    both succeed - which is to say it reports the wrong thing and skips the
    check it exists for. The braces make the whole conditional one operand.
    """
    official = [name for name in names if name in REQUIRED]
    aur = [name for name in names if name in AUR]
    steps: list[str] = []
    if official:
        steps.append("omarchy-pkg-add " + " ".join(shlex.quote(n) for n in official))
    if aur:
        steps.append(
            "{ omarchy-pkg-aur-accessible ||"
            " { echo 'The AUR is not reachable right now.'; exit 1; }; }"
        )
        steps.append("omarchy-pkg-aur-add " + " ".join(shlex.quote(n) for n in aur))
    if not steps:
        return ""
    return "echo 'Installing the input method packages...' && " + " && ".join(steps)


def install(names: list[str], runner=_run) -> tuple[bool, str]:
    """Hand the install to a terminal the user can answer sudo in.

    Returns `(started, detail)`. `started` means the terminal was launched, not
    that the packages are installed: the launcher detaches, so this cannot wait
    for the install and does not pretend to have. The detail says so, because a
    panel that reported "Packages installed" the instant a window opened would
    be making the same claim the old silent failure made.
    """
    script = install_script(names)
    if not script:
        return True, "nothing to install"
    code, _, stderr = runner([TERMINAL_LAUNCHER, script])
    if code == 0:
        return True, (
            "a terminal is installing " + ", ".join(names)
            + " and will ask for your password; refresh when it finishes"
        )
    reason = (stderr or "").strip() or (
        "could not be run" if code is UNAVAILABLE else f"exit status {code}"
    )
    return False, f"could not open a terminal to install in ({TERMINAL_LAUNCHER}: {reason})"


def install_here(names: list[str], runner=_run_attached) -> tuple[bool, str]:
    """Install in the terminal this command is already running in.

    The command-line path. Nothing is captured and nothing is detached, so sudo
    prompts where the person who typed the command is looking.
    """
    official = [name for name in names if name in REQUIRED]
    aur = [name for name in names if name in AUR]

    def failed(what: str, code) -> str:
        return (f"{what} could not be run" if code is UNAVAILABLE
                else f"{what} failed with exit status {code}")

    if official:
        code = runner(["omarchy", "pkg", "add", *official])
        if code != 0:
            return False, failed("omarchy pkg add", code)
    if aur:
        code = runner(["omarchy", "pkg", "aur", "accessible"])
        if code != 0:
            return False, "the AUR is not reachable right now"
        code = runner(["omarchy", "pkg", "aur", "add", *aur])
        if code != 0:
            return False, failed("omarchy pkg aur add", code)
    return True, "packages installed"
