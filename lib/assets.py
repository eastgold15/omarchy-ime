# lib/assets.py
"""Fetch external assets into a cache on the user's own machine.

Every asset comes from its own upstream. The Rime sources are pinned by commit,
and the pin is part of the cache path, so changing a pin is a new directory
rather than an update in place - there is no state where half the old revision
and half the new one are on disk. The dictionary is unpinnable (see DICTIONARY)
and lives at one path that is refreshed in place.

Nothing here is redistributed by this plugin: it downloads what each publisher
publishes, unmodified, and reads it where it lands.

A fetch that fails returns None. Callers treat that as "this feature has no data
yet" and carry on: no network problem may stop the user from typing.

Nothing an upstream sends is trusted on the strength of having arrived. Every
download is bounded before it is read, checked before it is accepted, and moved
into place in one step after it passes, so the worst a compromised or broken
upstream can do is leave the user with the copy they already had. What was
accepted is recorded beside the cache, because a supply-chain input that can
change silently is one nobody can review.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.request
import zlib
from pathlib import Path
from urllib.parse import urlsplit

from lib import dicts

# The prefix a notice wears when it is something that went wrong. lib/deploy.py
# takes its own WARNING from here so the two never drift apart: the panel and
# the command line both split notices on this string.
WARNING = "warning: "


class AssetRejected(Exception):
    """An upstream answered, and the answer was not one we may use.

    Distinct from a network error on purpose. A timeout is bad luck; this is a
    file that arrived and failed a check, which is the case worth naming in the
    message a user sees.
    """

PLUGIN_DIR = Path(__file__).resolve().parent.parent


SOURCES = {
    "rime-japanese": {
        "url": "https://github.com/gkovacs/rime-japanese.git",
        "revision": "4c1e65135459175136f380e90ba52acb40fdfb2d",
    },
    "rime-snomiao": {
        "url": "https://github.com/snomiao/rime-snomiao.git",
        "revision": "a2f3e7c7505abe567ec282ce61f754885c413e88",
    },
    # 白霜拼音 (rime-frost), for the cn_dicts_cell category dictionaries -
    # see lib/dicts.py. The clone also carries that project's schemas and its
    # own base tables; only the cell files named in SOURCE_FILES are taken.
    "rime-frost": {
        "url": "https://github.com/gaboolic/rime-frost.git",
        "revision": "64d24e89341aee133c0ad0f09b5f47a695e3d508",
    },
}

COMPLETE_MARKER = ".complete"

# The annotation dictionary is CC-CEDICT, downloaded from its publisher, MDBG,
# onto the user's own machine and read exactly as published. This plugin
# redistributes no dictionary data: the CC-BY-SA 4.0 attribution and share-alike
# obligations stay with the file and its publisher, and users get upstream's
# current release rather than a snapshot of ours.
#
# URL verified 2026-08-25 with a ranged GET: HTTP 206, Content-Type
# application/x-gzip, 3970915 bytes, header comments carrying `#! version=1` and
# `#! date=2026-08-25T08:12:38Z`.
DICTIONARY = {
    "url": "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz",
    # The published name minus .gz: the download is decompressed, and nothing
    # else about it is changed.
    "filename": "cedict_1_0_ts_utf-8_mdbg.txt",
}

# The file is not pinned, because there is nothing to pin to. MDBG publishes one
# URL that always serves the current release and no URL for an older one, so a
# recorded version could be checked but never requested. The header's own
# `#! date=` is therefore written into the completion marker as a record of what
# is cached, not as a pin, and the cache is instead re-checked on a schedule:
# once a copy is this many days old the next deploy downloads it again. Upstream
# publishes changes most days; a month keeps a desktop reasonably current without
# a download on every deploy.
MAX_AGE_DAYS = 30

# The header field that carries upstream's own release stamp.
VERSION_FIELD = "#! date="

# --- what a download is allowed to cost --------------------------------------
#
# Both caps are derived from the real file rather than guessed, and both are
# hard failures rather than truncations: a response that reaches either one is
# refused and nothing it wrote survives.
#
# Measured from the published file on 2026-08-25 (`#! date=2026-08-25T08:12:38Z`,
# `#! entries=124934`):
#
#     compressed    3_970_915 bytes as served
#     decompressed  9_839_666 bytes
#     ratio         2.48x
#
# CC-CEDICT gains entries at single-digit percent a year, so the margin here is
# measured in decades, not releases. The caps are ~8x and ~10x the measured
# sizes, and they are consistent with each other: a file that just fits the
# compressed cap decompresses at CC-CEDICT's own ratio to ~79MiB, which the
# decompressed cap still admits. That is the point of the pair - a compressed
# cap that could only be reached by a file the other cap rejects would be a cap
# in name only.
#
# To raise them: re-measure the published file, keep the same multiples, and say
# in the commit what the new measurement was. Do not raise one without the other.
MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 96 * 1024 * 1024

# How much is read from the socket at a time, and how much is decompressed at a
# time. The second one is the load-bearing number: `read()` on a decompression
# stream allocates whatever the stream expands to, so a gzip bomb read without a
# size is an out-of-memory before any cap gets a chance to look at the total.
# Reading a bounded block at a time means the cap is checked against a running
# total *before* the next allocation, and the largest single allocation this
# module can be made to perform is WRITE_CHUNK.
READ_CHUNK = 64 * 1024
WRITE_CHUNK = 256 * 1024

# Two clocks, because one is not enough. DOWNLOAD_TIMEOUT is what urllib is
# given, and urllib applies it per socket operation: it fires when the server
# goes quiet, and never while the server keeps sending. A server that sends one
# byte every few seconds keeps every read comfortably inside it and holds the
# deploy open for as long as it likes. DOWNLOAD_DEADLINE is the bound on the
# whole transfer, checked before every read, so the worst case is the deadline
# plus one idle timeout. The real file is under 4 MB and arrives in seconds;
# five minutes is not a budget anyone will notice from the wrong side.
DOWNLOAD_TIMEOUT = 60
DOWNLOAD_DEADLINE = 300

# --- what the file has to look like before it is believed --------------------
#
# There is no published checksum for CC-CEDICT and there cannot be one: MDBG
# serves the current release from a single URL and rebuilds it most days, so a
# hash committed here would be wrong by the following week and a pin would name
# a release that can no longer be requested. Nothing here pretends otherwise.
# What can be checked is that the bytes are the file they claim to be, and the
# published format is specific enough for that to mean something: a `# CC-CEDICT`
# banner, a block of `#!` metadata, then one entry per line in
# `Traditional Simplified [pin1 yin1] /gloss/gloss/` form, CRLF throughout.
BANNER = "# CC-CEDICT"

# Every one of these must be present and non-empty. `publisher`, `license` and
# `time` are published too but are not required: they carry no meaning this code
# acts on, and requiring a field nobody reads only invents a way to reject a
# good file.
REQUIRED_METADATA = ("version", "subversion", "format", "charset", "entries", "date")

# `ts` is traditional-then-simplified, which is the column order every entry is
# read in; a file in another format would be parsed wrongly rather than not at
# all. The charset is checked because the bytes are decoded as UTF-8 regardless
# of what the header says, and a header that disagrees is a file that is not the
# one this URL is supposed to serve.
EXPECTED_FORMAT = "ts"
EXPECTED_CHARSET = "utf-8"

# One entry: two whitespace-free columns, a bracketed pinyin field, and at least
# one slash-delimited gloss. Deliberately shape-only. Nothing here checks that
# the headwords are Han characters (CC-CEDICT carries entries like `X光` and
# `卡拉OK`), that the pinyin is a real syllable, or what a gloss says - those are
# judgements about the dictionary's content, and this plugin is not the editor
# of that dictionary. The check exists to tell CC-CEDICT apart from an HTML
# error page, a half-transferred file, or a different dictionary entirely.
ENTRY_PATTERN = re.compile(r"^\S+ \S+ \[[^\[\]]*\] /.+/$")

# A floor under the entry count, at about 80% of the 124_934 published on
# 2026-08-25. CC-CEDICT has only ever grown; a file claiming to be it with a
# fifth of the entries missing is a file something has happened to.
MIN_ENTRIES = 100_000

# The scheme and hosts a dictionary download may touch - on the first request
# and on every redirect after it. urllib follows redirects by default and does
# not care where to, so an upstream that answered `302 http://elsewhere/` would
# otherwise be followed off TLS and off the publisher's host without a word.
DICTIONARY_SCHEME = "https"
DICTIONARY_HOSTS = ("www.mdbg.net", "mdbg.net")

# For each source, the files worth extracting: path inside the clone -> path
# relative to the Rime directory. Named one by one rather than copied wholesale,
# because these repositories carry schemas the plugin does not deploy, and an
# unwanted schema left in the Rime directory is a schema Rime will load.
#
# Everything named here is copied byte for byte. A file this plugin wants
# changed is not edited on the way through: the change goes in a Rime
# customization file of the plugin's own beside it, and upstream's file arrives
# as upstream published it. Which is why `japanese_pinyin.schema.yaml`,
# `translate_en2jp.schema.yaml`, `japanese.dict.yaml` and `opencc/zhjp.json` are
# fetched rather than shipped: the repository used to carry edited copies of all
# four, and every edit in them turned out to be cosmetic (see NOTICE).
SOURCE_FILES = {
    "rime-japanese": {
        # The `japanese` dictionary and the three tables it imports, kept
        # together: the header names the tables, so it comes from the same
        # revision they do.
        "japanese.dict.yaml": "japanese.dict.yaml",
        "japanese.jmdict.dict.yaml": "japanese.jmdict.dict.yaml",
        "japanese.kana.dict.yaml": "japanese.kana.dict.yaml",
        "japanese.mozc.dict.yaml": "japanese.mozc.dict.yaml",
    },
    "rime-snomiao": {
        "Rime/japanese_chars.dict.yaml": "japanese_chars.dict.yaml",
        "Rime/japanese_pinyin.dict.yaml": "japanese_pinyin.dict.yaml",
        # japanese_tw_eng names japanese_pinyin and translate_en2jp as
        # dependencies and looks up through their prisms, so librime has to
        # build both schemas - which it can only do from their own schema files.
        "Rime/japanese_pinyin.schema.yaml": "japanese_pinyin.schema.yaml",
        "Rime/japanese_pinyin_simp.dict.yaml": "japanese_pinyin_simp.dict.yaml",
        "Rime/pinyin_simp.dict.yaml": "pinyin_simp.dict.yaml",
        "Rime/translate_en2jp.dict.yaml": "translate_en2jp.dict.yaml",
        "Rime/translate_en2jp.schema.yaml": "translate_en2jp.schema.yaml",
        "Rime/opencc/zhjp.json": "opencc/zhjp.json",
        "Rime/opencc/zhjp_daily.txt": "opencc/zhjp_daily.txt",
        "Rime/opencc/zhjp_word.txt": "opencc/zhjp_word.txt",
    },
    # Every cell dictionary in lib/dicts.CATALOG, installed under its own name
    # because Rime requires the file name to equal the `name:` inside it. The
    # whole set is fetched and installed while any cell is selected - the clone
    # is one fetch either way, and the files are small beside the schemas that
    # read them. Only the selected ones reach an `import_tables`, so only those
    # are ever compiled.
    "rime-frost": dicts.SOURCE_FILES,
}

# Which schema needs which of these sources is the schema's feature's business,
# declared once in lib/features.py beside the schema itself.


def cache_path(name: str, revision: str, cache_root: Path) -> Path:
    return Path(cache_root) / "sources" / name / revision


# How long a clone may take before it is given up on. Without it a stalled
# connection hangs `ime-manager deploy` for ever, and from the panel that is a
# spinner with no cancel and no explanation. The dictionary download has always
# had its own timeout; this is the same bound on the other half of the network.
FETCH_TIMEOUT = 300


def _git_fetch(url: str, revision: str, destination: str) -> None:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    # GIT_TERMINAL_PROMPT=0 and the askpass override together mean git fails
    # rather than waits. Run from the panel there is no terminal to answer a
    # credential prompt on, so a repository that asked for one would hang the
    # deploy instead of failing it - and a failed fetch is something this plugin
    # already knows how to carry on from.
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_ASKPASS"] = "true"
    environment["SSH_ASKPASS"] = "true"
    environment["GCM_INTERACTIVE"] = "never"

    def git(*arguments, capture=False):
        return subprocess.run(["git", *arguments], cwd=destination, check=True,
                              env=environment, timeout=FETCH_TIMEOUT,
                              capture_output=capture, text=capture)

    git("init", "-q")
    # fetch.fsckObjects checks every transferred object against its own hash as
    # it arrives, so a transfer that does not hash to what was asked for fails
    # in git rather than landing in the cache. It is off by default.
    git("-c", "fetch.fsckObjects=true",
        "fetch", "-q", "--depth", "1", url, revision)
    git("checkout", "-q", "FETCH_HEAD")
    # The pin is the only thing that makes this a supply-chain input rather than
    # "whatever the server felt like sending", and a pin nobody checks is a
    # comment. Git names objects by their content, so a HEAD that equals the
    # pinned commit is the whole tree being the tree that commit names - which
    # is why these sources need no separate checksum and the dictionary does.
    #
    # Raised rather than returned: `ensure` discards the directory and reports
    # the source as unfetched, which is a state the deploy already handles.
    head = (git("rev-parse", "HEAD", capture=True).stdout or "").strip()
    if head != revision:
        raise AssetRejected(
            f"{url} checked out {head or 'nothing'} rather than the pinned"
            f" {revision}"
        )
    # The pin is the cache key, so nothing ever asks this clone for history. On
    # the two sources this plugin fetches that is 103MB of .git per copy, kept
    # for ever, for files that are already checked out beside it.
    shutil.rmtree(destination / ".git", ignore_errors=True)


def _drop_other_revisions(name: str, keep: Path, cache_root: Path) -> None:
    """Remove this source's other cached revisions.

    `cache_path` keys on the pinned commit, so bumping a pin is a new directory
    rather than an update in place - which is what makes a pin change atomic,
    and what left every previous revision on disk for ever.
    """
    parent = Path(cache_root) / "sources" / name
    if not parent.is_dir():
        return
    for directory in parent.iterdir():
        if directory.is_dir() and directory != keep:
            shutil.rmtree(directory, ignore_errors=True)


def ensure(name: str, cache_root: Path, fetcher=_git_fetch) -> Path | None:
    """Return the cached asset directory, fetching it if needed.

    The completion marker is written last. A directory without it is a fetch that
    died partway, and is discarded rather than trusted.
    """
    source = SOURCES[name]
    target = cache_path(name, source["revision"], cache_root)
    if (target / COMPLETE_MARKER).is_file():
        return target
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        fetcher(source["url"], source["revision"], str(target))
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        return None
    (target / COMPLETE_MARKER).write_text("", encoding="utf-8")
    _drop_other_revisions(name, target, cache_root)
    return target


def default_cache_root() -> Path:
    """Where assets are cached when no caller says otherwise."""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "omarchy-ime"


def dictionary_url() -> str:
    return DICTIONARY["url"]


def dictionary_path(cache_root: Path) -> Path:
    """Where the dictionary lives, whether or not it has been downloaded.

    This is the one place that answers the question, so ime-manager can report
    the path and generate.py can write it into the schema without either of them
    re-deriving the layout. It does not depend on which upstream release is
    cached: the absolute path is baked into the Rime schema at deploy time, and a
    path that moved with every upstream refresh would leave a deployed schema
    pointing at a file that is no longer there.
    """
    return Path(cache_root) / "dictionary" / DICTIONARY["filename"]


def default_dictionary_path() -> Path:
    return dictionary_path(default_cache_root())


# --- transport ---------------------------------------------------------------


def require_secure_url(url: str) -> None:
    """Raise unless `url` is HTTPS to the dictionary's publisher.

    Called on the URL this module is about to open and again on every redirect
    it is offered, so there is no hop at which the connection can be talked down
    to plain HTTP or off onto some other host. Both halves matter: HTTPS to the
    wrong host is as much a substitution as HTTP to the right one.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() != DICTIONARY_SCHEME:
        raise AssetRejected(
            f"{url} is not {DICTIONARY_SCHEME}, so it will not be fetched"
        )
    host = (parts.hostname or "").lower()
    if host not in DICTIONARY_HOSTS:
        # Compared whole rather than by suffix. A suffix test also accepts
        # `mdbg.net.example.invalid`, which is the shape this mistake takes
        # wherever it is made.
        raise AssetRejected(
            f"{url} is not on {' or '.join(DICTIONARY_HOSTS)}, so it will not"
            " be fetched"
        )


class _SecureRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only while they stay HTTPS on the publisher's host."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        require_secure_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _secure_opener() -> urllib.request.OpenerDirector:
    """An opener with the redirect policy in place of urllib's default one."""
    return urllib.request.build_opener(_SecureRedirects())


def _open_https(url: str, timeout: int = DOWNLOAD_TIMEOUT):
    require_secure_url(url)
    return _secure_opener().open(url, timeout=timeout)


class _BoundedReader:
    """A response that can only ever hand over so many bytes.

    The bound is on what is actually read. `Content-Length` is not consulted for
    anything: it is a header the sender writes, so a sender worth defending
    against is a sender who writes whatever number gets past the check. Passing
    the bound raises rather than stopping early - a truncated dictionary that
    looked like a successful download would be the worse outcome.
    """

    def __init__(self, response, limit: int, deadline: float | None = None,
                 clock=time.monotonic):
        self._response = response
        self._limit = limit
        self._clock = clock
        self._deadline = None if deadline is None else clock() + deadline
        self._budget = deadline
        self.read_bytes = 0

    def read(self, size: int = -1) -> bytes:
        # A negative size means "to EOF", which is exactly the read this class
        # exists to prevent, so it becomes one chunk instead.
        if size is None or size < 0 or size > READ_CHUNK:
            size = READ_CHUNK
        # Checked before the read rather than after: a read that blocks is
        # bounded by the socket timeout, and the check has to come around
        # again before the next one can start.
        if self._deadline is not None and self._clock() > self._deadline:
            raise AssetRejected(
                f"the download did not finish within {self._budget} seconds;"
                " a response that arrives this slowly is not one worth waiting for"
            )
        chunk = self._response.read(size)
        self.read_bytes += len(chunk)
        if self.read_bytes > self._limit:
            raise AssetRejected(
                f"the response passed {self._limit} compressed bytes, which is"
                " far more than the dictionary has ever been"
            )
        return chunk


def _create_exclusively(path: Path):
    """Open `path` for writing only if nothing stands there yet.

    A plain `open(path, "wb")` follows a symlink standing at `path` and
    truncates whatever it points to. The download's destination is a file
    nothing has written yet, so the only honest way to open it is to insist on
    creating it: O_EXCL refuses an existing file, and refuses a symlink whether
    or not its target exists, and O_NOFOLLOW is belt to that brace. The name is
    already unguessable - `ensure_dictionary` draws it fresh each time - so this
    is the guard for the case where the guess was somehow right.
    """
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
        )
    except FileExistsError as error:
        raise AssetRejected(
            f"something already stands where the download would land ({path.name});"
            " nothing was written over it"
        ) from error
    except OSError as error:
        raise AssetRejected(f"cannot create {path.name}: {error}") from error
    return os.fdopen(descriptor, "wb")


def download(url: str, destination: str, open_url=_open_https,
             clock=time.monotonic) -> None:
    """Download the published gzip and write it out decompressed.

    Decompressing is not a modification of the data: it is the same bytes the
    publisher compressed. Nothing else is done to them.

    Both directions are bounded. The compressed side is capped by
    `_BoundedReader`; the decompressed side is read one block at a time and the
    running total is checked against the cap before the block is written and
    before the next one is asked for, so a gzip bomb is refused after WRITE_CHUNK
    bytes rather than after it has filled memory or the disk. The gzip framing
    does the rest: a stream that ends early or whose CRC does not match is not a
    file this returns.

    What lands at `destination` is not trusted yet, and this does not clean it
    up on failure - `ensure_dictionary` owns that path and unlinks it. Nothing
    here writes anywhere a reader would look.
    """
    require_secure_url(url)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open_url(url) as response:
        reader = _BoundedReader(response, MAX_COMPRESSED_BYTES,
                                deadline=DOWNLOAD_DEADLINE, clock=clock)
        with _create_exclusively(destination) as handle:
            with gzip.GzipFile(fileobj=reader, mode="rb") as stream:
                while True:
                    try:
                        block = stream.read(WRITE_CHUNK)
                    except AssetRejected:
                        raise
                    except (OSError, EOFError, zlib.error) as error:
                        raise AssetRejected(
                            f"the response is not a complete gzip stream ({error})"
                        ) from error
                    if not block:
                        break
                    if written + len(block) > MAX_DECOMPRESSED_BYTES:
                        raise AssetRejected(
                            f"the response expands past {MAX_DECOMPRESSED_BYTES}"
                            " bytes, which is far more than the dictionary has"
                            " ever been"
                        )
                    handle.write(block)
                    written += len(block)
            # Flushed to the platter before anyone is told the download worked:
            # the completion marker is a promise about a file, and a promise
            # that outlives the file it describes is how a cache lies after a
            # power cut.
            handle.flush()
            os.fsync(handle.fileno())
    if not written:
        raise AssetRejected("the response decompressed to nothing")


# --- what arrived, and whether it is the dictionary --------------------------


def verify(path: Path) -> dict:
    """Check that `path` is a CC-CEDICT release, and describe the one it is.

    Raises `AssetRejected` with a reason a user can act on, or returns the record
    that goes beside the cache: upstream's own `version`, `subversion`, `format`,
    `charset`, `date` and `entries`, plus the size and the sha256 of the bytes
    being accepted. That hash is not a pin - there is nothing to pin to - it is
    what makes "the dictionary changed" a statement someone can check afterwards.

    Everything checked here is structure. A file that passes is one shaped like
    CC-CEDICT and consistent with its own header; it is not one whose glosses
    have been reviewed, and no check in this module could make it so.
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise AssetRejected(f"it could not be read back ({error})") from error
    if not raw:
        raise AssetRejected("it is empty")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        # The header promises UTF-8, so bytes that are not UTF-8 are not this
        # file - an error page in some other encoding, a binary, or a transfer
        # that went wrong somewhere gzip's CRC could not see.
        raise AssetRejected(f"it is not UTF-8 ({error})") from error
    if text.startswith("﻿"):
        text = text[1:]

    lines = text.split("\r\n")
    if lines and lines[-1] == "":
        # The published file ends without a final CRLF. Accept both rather than
        # make a byte upstream could change either way load-bearing.
        lines.pop()
    for number, line in enumerate(lines, start=1):
        if "\n" in line or "\r" in line:
            raise AssetRejected(
                "it does not use CRLF line endings, which CC-CEDICT does"
                f" (line {number})"
            )
    if not lines or not lines[0].startswith(BANNER):
        raise AssetRejected(
            f"it does not begin with the {BANNER!r} banner, so it is not the"
            " file this URL is supposed to serve"
        )

    metadata: dict[str, str] = {}
    index = 0
    while index < len(lines) and lines[index].startswith("#"):
        line = lines[index]
        index += 1
        if line.startswith("#!"):
            field, separator, value = line[2:].strip().partition("=")
            if separator:
                metadata[field.strip()] = value.strip()
    absent = [field for field in REQUIRED_METADATA if not metadata.get(field)]
    if absent:
        raise AssetRejected(
            "its header is missing " + ", ".join(f"#! {name}=" for name in absent)
        )
    if metadata["format"] != EXPECTED_FORMAT:
        raise AssetRejected(
            f"its header declares format={metadata['format']!r}, not"
            f" {EXPECTED_FORMAT!r}"
        )
    if metadata["charset"].lower() != EXPECTED_CHARSET:
        raise AssetRejected(
            f"its header declares charset={metadata['charset']!r}, not UTF-8"
        )
    for field in ("version", "subversion", "entries"):
        if not metadata[field].isdigit():
            raise AssetRejected(
                f"its header's {field} is not a number: {metadata[field]!r}"
            )
    declared = int(metadata["entries"])

    body = lines[index:]
    stray = next(
        (number for number, line in enumerate(body, start=index + 1)
         if line.startswith("#")),
        None,
    )
    if stray is not None:
        # The header is a block at the top, not comments sprinkled through the
        # file. One that interleaves them is not the file this reads.
        raise AssetRejected(f"line {stray} is a comment inside the entry body")
    if declared < MIN_ENTRIES:
        raise AssetRejected(
            f"its header declares {declared} entries, and CC-CEDICT has not had"
            f" fewer than {MIN_ENTRIES} for years"
        )
    if len(body) != declared:
        # The count upstream wrote against the count actually parsed. Someone
        # who rewrote the whole file would keep these two consistent, so this is
        # not what catches a substitution - it catches the file arriving in a
        # state upstream did not publish it in.
        raise AssetRejected(
            f"its header declares {declared} entries but the body carries"
            f" {len(body)}"
        )
    for number, line in enumerate(body, start=index + 1):
        if not ENTRY_PATTERN.match(line):
            raise AssetRejected(
                f"line {number} is not a CC-CEDICT entry: {line[:60]!r}"
            )

    return {
        "version": metadata["version"],
        "subversion": metadata["subversion"],
        "format": metadata["format"],
        "charset": metadata["charset"],
        "date": metadata["date"],
        "entries": declared,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def cached_release(cache_root: Path) -> dict | None:
    """The record of what was accepted into the cache, or None.

    Read for reporting, so anything unreadable answers None rather than raising:
    not knowing which release is cached is a worse status line, not a failure.
    """
    marker = dictionary_path(cache_root).parent / COMPLETE_MARKER
    try:
        written = marker.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        record = json.loads(written)
    except ValueError:
        # Markers written before this record existed carried the bare `#! date=`
        # stamp and nothing else. Still worth reporting.
        stamp = written.strip()
        return {"date": stamp} if stamp else None
    return record if isinstance(record, dict) else None


def release_summary(record: dict | None) -> str:
    """One line naming a release, for `status` and for deploy's output."""
    if not record:
        return "not downloaded"
    parts = []
    if record.get("version"):
        parts.append(f"CC-CEDICT {record['version']}.{record.get('subversion', 0)}")
    else:
        parts.append("CC-CEDICT")
    if record.get("date"):
        parts.append(f"dated {record['date']}")
    if record.get("entries"):
        parts.append(f"{record['entries']} entries")
    if record.get("sha256"):
        # Enough digest to tell two releases apart by eye. The marker beside the
        # cache holds all of it, for anyone comparing them properly.
        parts.append(f"sha256 {record['sha256'][:12]}")
    return ", ".join(parts)


def upstream_version(path: Path) -> str:
    """Return the release stamp CC-CEDICT writes into its header, or empty.

    Read for the record only - the plugin cannot ask upstream for a particular
    release - so anything unreadable answers empty rather than raising.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.startswith("#"):
                    break
                if line.startswith(VERSION_FIELD):
                    return line[len(VERSION_FIELD):].strip()
    except OSError:
        return ""
    return ""


def _older_than(path: Path, days: int) -> bool:
    try:
        return time.time() - path.stat().st_mtime > days * 86400
    except OSError:
        return True


def ensure_dictionary(cache_root: Path, fetcher=download, notice=None) -> Path | None:
    """Return the cached dictionary file, downloading it if needed, or None.

    Failure is never raised. A dictionary that cannot be downloaded leaves the
    annotation filter reading an absent file, which it treats as empty, so a
    network problem costs the glosses and nothing else - and a refresh that
    fails, or one that arrives as something other than CC-CEDICT, keeps the copy
    already on disk rather than throwing it away.

    The download lands beside the cached file under a name nothing reads, is
    checked there, and is moved onto the cached path in one `os.replace` only
    once it has passed every check. There is no moment at which a reader can see
    half a dictionary: either the previous file is there or the new one is.

    The completion marker is written last and holds the record of what was
    accepted, so a directory without it is a download that died partway and is
    discarded rather than trusted.

    `notice` is called with lines for the user: when a release arrives, when a
    refresh brings a *different* release than the one that was cached, and when
    a download is refused and why. It is never asked anything. A deploy that
    stopped to interrogate someone about a dictionary refresh would be a worse
    plugin than one that quietly kept the old file; being told is the point.
    """
    def say(message: str) -> None:
        if notice is not None:
            notice(message)

    target = dictionary_path(cache_root)
    marker = target.parent / COMPLETE_MARKER
    cached = marker.is_file() and target.is_file()
    previous = cached_release(cache_root) if cached else None
    if cached and not _older_than(marker, MAX_AGE_DAYS):
        return target
    if not cached:
        shutil.rmtree(target.parent, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Beside the target, because `os.replace` is atomic only within one
    # filesystem. The name is drawn fresh rather than derived from the pid: a
    # pid is public, so a name built from it is a name another process can
    # plant a symlink at before the download starts, and the write would then
    # go wherever the link pointed. Sixteen random hex digits also keep two
    # deploys at once out of each other's half-finished file, which is all the
    # pid was ever for. `download` refuses to open a name that already exists,
    # so even a correct guess costs the guesser nothing but a refused deploy.
    pending = target.with_name(f"{target.name}.{secrets.token_hex(8)}.part")
    try:
        fetcher(dictionary_url(), str(pending))
        if not pending.is_file():
            raise AssetRejected("the download wrote nothing")
        record = verify(pending)
    except Exception as error:
        pending.unlink(missing_ok=True)
        if cached:
            say(WARNING + f"dictionary: the refresh was refused ({error});"
                f" keeping {release_summary(previous)}")
            return target
        say(WARNING + f"dictionary: the download was refused ({error});"
            " glosses stay empty until a deploy gets a good copy")
        return None
    marker.unlink(missing_ok=True)
    os.replace(pending, target)
    marker.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    if previous is None:
        say(f"dictionary: {release_summary(record)}")
    elif previous.get("sha256") != record["sha256"]:
        # A dictionary that changed under the user says so in the deploy that
        # changed it. An upstream that can be swapped without anyone noticing is
        # the whole objection this record answers.
        say(f"dictionary: upstream changed from {release_summary(previous)}"
            f" to {release_summary(record)}")
    return target
