# tests/test_assets.py
import gzip
import hashlib
import io
import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lib import assets

ROOT = Path(__file__).resolve().parent.parent


class CachePathTests(unittest.TestCase):
    def test_revision_is_part_of_the_path(self):
        path = assets.cache_path("rime-japanese", "abc123", Path("/cache"))
        self.assertIn("abc123", str(path))

    def test_different_revisions_do_not_share_a_directory(self):
        one = assets.cache_path("rime-japanese", "aaa", Path("/cache"))
        two = assets.cache_path("rime-japanese", "bbb", Path("/cache"))
        self.assertNotEqual(one, two)


class EnsureTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.cache = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_populated_cache_is_not_fetched_again(self):
        revision = assets.SOURCES["rime-japanese"]["revision"]
        target = assets.cache_path("rime-japanese", revision, self.cache)
        target.mkdir(parents=True)
        (target / ".complete").write_text("")
        calls = []
        assets.ensure("rime-japanese", self.cache, lambda *a: calls.append(a))
        self.assertEqual(calls, [])

    def test_a_fetch_marks_the_cache_complete(self):
        def fetcher(url, revision, destination):
            Path(destination).mkdir(parents=True, exist_ok=True)

        path = assets.ensure("rime-japanese", self.cache, fetcher)
        self.assertTrue((path / ".complete").is_file())

    def test_a_failed_fetch_returns_none_and_leaves_no_complete_marker(self):
        def fetcher(url, revision, destination):
            raise RuntimeError("network down")

        revision = assets.SOURCES["rime-japanese"]["revision"]
        self.assertIsNone(assets.ensure("rime-japanese", self.cache, fetcher))
        marker = assets.cache_path("rime-japanese", revision, self.cache) / ".complete"
        self.assertFalse(marker.exists())


class GitFetchTests(unittest.TestCase):
    """The clone is pinned by commit, and the pin is checked after checkout.

    Nothing here runs git: the subprocess call is recorded.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.destination = Path(self._tmp.name) / "clone"
        self.addCleanup(self._tmp.cleanup)
        self.revision = "a" * 40

    def _run(self, checked_out):
        calls = []

        def runner(arguments, **kwargs):
            calls.append(list(arguments))
            return mock.Mock(returncode=0, stdout=checked_out + "\n", stderr="")

        with mock.patch.object(assets.subprocess, "run", side_effect=runner):
            assets._git_fetch("https://example.invalid/x.git", self.revision,
                              str(self.destination))
        return calls

    def test_the_checked_out_commit_is_verified_against_the_pin(self):
        calls = self._run(self.revision)
        self.assertIn(["git", "rev-parse", "HEAD"], calls)

    def test_a_clone_that_checked_out_something_else_is_refused(self):
        with self.assertRaises(assets.AssetRejected):
            self._run("b" * 40)

    def test_the_fetch_is_shallow_and_names_the_pinned_commit(self):
        fetch = [c for c in self._run(self.revision) if "fetch" in c][0]
        self.assertIn("--depth", fetch)
        self.assertIn(self.revision, fetch)

    def test_transferred_objects_are_checked_against_their_hashes(self):
        fetch = [c for c in self._run(self.revision) if "fetch" in c][0]
        self.assertIn("fetch.fsckObjects=true", fetch)

    def test_every_pinned_revision_is_a_full_commit_hash(self):
        # An abbreviated or symbolic revision is not a pin: it can resolve to
        # different content later.
        for name, source in assets.SOURCES.items():
            with self.subTest(name):
                self.assertRegex(source["revision"], r"^[0-9a-f]{40}$")


class UpstreamDictionaryTests(unittest.TestCase):
    """The dictionary comes from its publisher, not from us.

    CC-CEDICT is CC-BY-SA 4.0. The plugin downloads the published file onto the
    user's machine and reads it as published, so it redistributes no third-party
    data and carries no share-alike obligation of its own.
    """

    def test_the_url_is_the_publishers_own_distribution(self):
        url = assets.dictionary_url()
        self.assertIn("mdbg.net", url)
        self.assertTrue(url.endswith(".gz"), url)

    def test_nothing_of_ours_is_served_as_the_dictionary(self):
        url = assets.dictionary_url()
        self.assertNotIn("omarchy-ime", url)
        self.assertNotIn("/releases/download/", url)

    def test_the_cached_file_keeps_upstreams_name(self):
        # Decompressed, so the cached name is the published name without .gz.
        published = assets.dictionary_url().rsplit("/", 1)[-1]
        self.assertEqual(assets.DICTIONARY["filename"], published[: -len(".gz")])

    def test_the_path_is_under_the_cache_root_and_named_by_upstream(self):
        path = assets.dictionary_path(Path("/cache"))
        self.assertTrue(str(path).startswith("/cache/"))
        self.assertEqual(path.name, assets.DICTIONARY["filename"])

    def test_the_repository_ships_no_dictionary_pin(self):
        # There is one upstream URL and it always serves the current release, so
        # there is no version to pin to and no pin file to keep.
        self.assertFalse((ROOT / "sources" / "dictionary.rev").exists())

    def test_the_recheck_window_is_a_real_number_of_days(self):
        self.assertGreater(assets.MAX_AGE_DAYS, 0)

    def test_the_default_path_is_absolute(self):
        self.assertTrue(assets.default_dictionary_path().is_absolute())


# The real file, measured 2026-08-25: 9_839_666 bytes decompressed, 3_970_915
# compressed, 124_934 entries. Every cap is derived from those three numbers.
OBSERVED_BYTES = 9_839_666
OBSERVED_COMPRESSED = 3_970_915
OBSERVED_ENTRIES = 124_934


class LimitTests(unittest.TestCase):
    """Both caps are finite, and both leave room for the file to grow."""

    def test_the_compressed_cap_is_finite_and_above_the_published_size(self):
        self.assertGreater(assets.MAX_COMPRESSED_BYTES, OBSERVED_COMPRESSED)
        self.assertLess(assets.MAX_COMPRESSED_BYTES, 1 << 30)

    def test_the_decompressed_cap_is_finite_and_above_the_published_size(self):
        self.assertGreater(assets.MAX_DECOMPRESSED_BYTES, OBSERVED_BYTES)
        self.assertLess(assets.MAX_DECOMPRESSED_BYTES, 1 << 30)

    def test_the_decompressed_cap_admits_a_file_that_fills_the_compressed_cap(self):
        # Text at CC-CEDICT's own ratio (about 2.5x). A compressed cap that
        # could only ever be reached by a file the decompressed cap rejects
        # would make one of the two caps a lie.
        ratio = OBSERVED_BYTES / OBSERVED_COMPRESSED
        self.assertGreater(assets.MAX_DECOMPRESSED_BYTES,
                           assets.MAX_COMPRESSED_BYTES * ratio)

    def test_the_entry_floor_is_below_the_published_count(self):
        self.assertLess(assets.MIN_ENTRIES, OBSERVED_ENTRIES)
        self.assertGreater(assets.MIN_ENTRIES, 0)

    def test_a_single_read_cannot_allocate_more_than_a_chunk(self):
        self.assertLessEqual(assets.WRITE_CHUNK, 1 << 20)
        self.assertLessEqual(assets.READ_CHUNK, 1 << 20)


# A CC-CEDICT file shaped exactly like the published one: a `#` banner, `#!`
# metadata, then entries, all CRLF - and no newline after the last entry, which
# is how the real file ends.
ENTRIES = [
    "你好 你好 [ni3 hao3] /hello/hi/",
    "一 一 [yi1] /one/1/single/",
    "中國 中国 [Zhong1 guo2] /China/",
]
DATE = "2026-08-25T08:12:38Z"


def cedict(entries=ENTRIES, *, banner="# CC-CEDICT", version="1", subversion="0",
           fmt="ts", charset="UTF-8", date=DATE, declared=None, fields=True,
           eol="\r\n", trailing_newline=False) -> str:
    lines = [
        banner,
        "# Community maintained free Chinese-English dictionary.",
        "# ",
        "# Published by MDBG",
    ]
    if fields:
        lines += [
            f"#! version={version}",
            f"#! subversion={subversion}",
            f"#! format={fmt}",
            f"#! charset={charset}",
            f"#! entries={len(entries) if declared is None else declared}",
            "#! publisher=MDBG",
            "#! license=https://creativecommons.org/licenses/by-sa/4.0/",
            f"#! date={date}",
            "#! time=1787645558",
        ]
    lines += list(entries)
    text = eol.join(lines)
    return text + eol if trailing_newline else text


def gzipped(text: str) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as handle:
        handle.write(text.encode("utf-8"))
    return buffer.getvalue()


class TransportTests(unittest.TestCase):
    """HTTPS to the publisher's own host, at every hop."""

    def test_the_published_url_passes_its_own_policy(self):
        assets.require_secure_url(assets.dictionary_url())

    def test_the_published_url_is_https(self):
        self.assertTrue(assets.dictionary_url().startswith("https://"))

    def test_plain_http_is_refused(self):
        with self.assertRaises(assets.AssetRejected):
            assets.require_secure_url(
                "http://www.mdbg.net/chinese/export/cedict/cedict.txt.gz")

    def test_another_host_is_refused(self):
        with self.assertRaises(assets.AssetRejected):
            assets.require_secure_url("https://example.invalid/cedict.txt.gz")

    def test_a_lookalike_host_is_refused(self):
        with self.assertRaises(assets.AssetRejected):
            assets.require_secure_url("https://www.mdbg.net.evil.invalid/x.gz")

    def test_a_file_url_is_refused(self):
        with self.assertRaises(assets.AssetRejected):
            assets.require_secure_url("file:///etc/passwd")

    def _redirect(self, newurl):
        handler = assets._SecureRedirects()
        request = assets.urllib.request.Request(assets.dictionary_url())
        return handler.redirect_request(
            request, io.BytesIO(b""), 302, "Found", {}, newurl)

    def test_a_redirect_may_not_downgrade_to_http(self):
        with self.assertRaises(assets.AssetRejected):
            self._redirect("http://www.mdbg.net/chinese/export/cedict/x.gz")

    def test_a_redirect_may_not_leave_the_publishers_host(self):
        with self.assertRaises(assets.AssetRejected):
            self._redirect("https://cdn.example.invalid/x.gz")

    def test_a_redirect_within_the_publishers_host_is_followed(self):
        request = self._redirect("https://www.mdbg.net/chinese/export/cedict/y.gz")
        self.assertIsNotNone(request)

    def test_the_opener_carries_the_redirect_policy(self):
        opener = assets._secure_opener()
        self.assertTrue(
            any(isinstance(h, assets._SecureRedirects) for h in opener.handlers))


class DownloadTests(unittest.TestCase):
    """The published file is gzipped; nothing here touches the network."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _serve(self, blob: bytes):
        self.served = 0

        class Response(io.BytesIO):
            def read(inner, size=-1):
                chunk = io.BytesIO.read(inner, size)
                self.served += len(chunk)
                return chunk

        return lambda url, timeout=None: Response(blob)

    def _download(self, blob: bytes, destination=None):
        destination = destination or (self.tmp / "out" / "cedict.txt")
        assets.download(assets.dictionary_url(), str(destination),
                        open_url=self._serve(blob))
        return destination

    def test_a_gzipped_source_lands_decompressed(self):
        text = cedict()
        # Read as bytes: text mode would translate the CRLF the check depends on.
        self.assertEqual(self._download(gzipped(text)).read_bytes(),
                         text.encode("utf-8"))

    def test_the_bytes_are_upstreams_own_rather_than_a_conversion(self):
        # Byte for byte as published: only the gzip wrapper is removed.
        text = cedict()
        self.assertEqual(self._download(gzipped(text)).read_bytes(),
                         text.encode("utf-8"))

    def test_the_url_is_checked_before_anything_is_opened(self):
        opened = []

        def open_url(url, timeout=None):
            opened.append(url)
            return io.BytesIO(b"")

        with self.assertRaises(assets.AssetRejected):
            assets.download("http://www.mdbg.net/x.gz", str(self.tmp / "out.txt"),
                            open_url=open_url)
        self.assertEqual(opened, [])

    def test_a_response_longer_than_the_compressed_cap_is_refused(self):
        blob = gzipped(cedict())
        with mock.patch.object(assets, "MAX_COMPRESSED_BYTES", 64):
            with self.assertRaises(assets.AssetRejected):
                self._download(blob)

    def test_the_compressed_cap_stops_the_read_rather_than_truncating_it(self):
        blob = gzipped(cedict()) + b"\0" * (4 << 20)
        with mock.patch.object(assets, "MAX_COMPRESSED_BYTES", 4096):
            with self.assertRaises(assets.AssetRejected):
                self._download(blob)
        # Stopped near the cap, not after draining the response.
        self.assertLess(self.served, 4096 + assets.READ_CHUNK + 1)

    def test_a_drip_fed_response_is_given_up_on(self):
        # Two bytes per read and a clock that says ten seconds passed each
        # time: the per-read socket timeout never fires, so only a
        # whole-transfer deadline can end this. Two rather than one because
        # gzip asks for its two magic bytes in a single read, as a real
        # HTTPResponse would deliver them; a one-byte short read would be
        # refused as "not gzip" before the deadline ever got a look.
        blob = gzipped(cedict())
        clock = {"now": 1000.0}

        class Drip(io.BytesIO):
            def read(inner, size=-1):
                clock["now"] += 10.0
                chunk = io.BytesIO.read(inner, min(2, size if size > 0 else 2))
                self.served += len(chunk)
                return chunk

        self.served = 0
        with mock.patch.object(assets, "DOWNLOAD_DEADLINE", 30):
            with self.assertRaises(assets.AssetRejected) as caught:
                assets.download(assets.dictionary_url(),
                                str(self.tmp / "out" / "cedict.txt"),
                                open_url=lambda url, timeout=None: Drip(blob),
                                clock=lambda: clock["now"])
        self.assertIn("30", str(caught.exception))
        # Gave up after a few reads, not after draining the whole response.
        self.assertLess(self.served, 16)
        self.assertLess(self.served, len(blob))

    def test_the_deadline_is_a_whole_transfer_bound_not_an_idle_one(self):
        self.assertLess(assets.DOWNLOAD_TIMEOUT, assets.DOWNLOAD_DEADLINE)

    def test_a_symlink_standing_at_the_destination_is_not_followed(self):
        victim = self.tmp / "victim"
        victim.write_bytes(b"precious")
        destination = self.tmp / "out" / "cedict.txt.part"
        destination.parent.mkdir()
        destination.symlink_to(victim)
        with self.assertRaises(assets.AssetRejected):
            self._download(gzipped(cedict()), destination)
        self.assertEqual(victim.read_bytes(), b"precious")
        self.assertTrue(destination.is_symlink(), "the link was not replaced")

    def test_an_existing_file_at_the_destination_is_not_truncated(self):
        destination = self.tmp / "out" / "cedict.txt.part"
        destination.parent.mkdir()
        destination.write_bytes(b"someone else's download")
        with self.assertRaises(assets.AssetRejected):
            self._download(gzipped(cedict()), destination)
        self.assertEqual(destination.read_bytes(), b"someone else's download")

    def test_a_gzip_bomb_is_refused(self):
        bomb = gzipped("\0" * (8 << 20))
        self.assertLess(len(bomb), 1 << 16, "the bomb must really be a bomb")
        with mock.patch.object(assets, "MAX_DECOMPRESSED_BYTES", 1 << 20):
            with self.assertRaises(assets.AssetRejected):
                self._download(bomb)

    def test_a_gzip_bomb_is_refused_before_it_is_all_written(self):
        bomb = gzipped("\0" * (8 << 20))
        destination = self.tmp / "out" / "cedict.txt"
        with mock.patch.object(assets, "MAX_DECOMPRESSED_BYTES", 1 << 20):
            with self.assertRaises(assets.AssetRejected):
                self._download(bomb, destination)
        # The running total is checked as it goes, so at most one chunk gets
        # past the cap - not the whole 8MB.
        self.assertLessEqual(destination.stat().st_size,
                             (1 << 20) + assets.WRITE_CHUNK)

    def test_a_truncated_gzip_stream_is_refused(self):
        blob = gzipped(cedict())
        with self.assertRaises(assets.AssetRejected):
            self._download(blob[: len(blob) // 2])

    def test_a_corrupted_gzip_stream_is_refused(self):
        blob = bytearray(gzipped(cedict()))
        blob[-5] ^= 0xFF  # break the trailing CRC
        with self.assertRaises(assets.AssetRejected):
            self._download(bytes(blob))

    def test_an_html_error_page_is_refused(self):
        with self.assertRaises(assets.AssetRejected):
            self._download(b"<html><body>503 Service Unavailable</body></html>")

    def test_an_empty_response_is_refused(self):
        with self.assertRaises(assets.AssetRejected):
            self._download(b"")


class VerifyTests(unittest.TestCase):
    """What a file has to look like before it is allowed to replace the cache."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patch = mock.patch.object(assets, "MIN_ENTRIES", 1)
        patch.start()
        self.addCleanup(patch.stop)

    def _written(self, content) -> Path:
        path = self.tmp / "cedict.txt"
        if isinstance(content, str):
            content = content.encode("utf-8")
        path.write_bytes(content)
        return path

    def _rejects(self, content, fragment=""):
        with self.assertRaises(assets.AssetRejected) as caught:
            assets.verify(self._written(content))
        if fragment:
            self.assertIn(fragment, str(caught.exception))

    def test_a_published_shaped_file_is_accepted(self):
        record = assets.verify(self._written(cedict()))
        self.assertEqual(record["entries"], len(ENTRIES))
        self.assertEqual(record["date"], DATE)
        self.assertEqual(record["version"], "1")
        self.assertEqual(record["subversion"], "0")

    def test_a_trailing_newline_is_accepted_too(self):
        # The real file ends without one; do not make that load-bearing.
        assets.verify(self._written(cedict(trailing_newline=True)))

    def test_the_record_carries_the_sha256_of_the_accepted_bytes(self):
        text = cedict()
        record = assets.verify(self._written(text))
        self.assertEqual(record["sha256"],
                         hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(record["bytes"], len(text.encode("utf-8")))

    def test_an_empty_file_is_refused(self):
        self._rejects(b"", "empty")

    def test_an_html_error_page_is_refused(self):
        self._rejects("<html><head><title>Error</title></head></html>\r\n")

    def test_a_different_dictionary_is_refused(self):
        self._rejects(cedict(banner="# EDICT2"))

    def test_bytes_that_are_not_utf8_are_refused(self):
        self._rejects(cedict().encode("utf-8").replace(b"[ni3", b"[\xff\xfe"),
                      "UTF-8")

    def test_unix_line_endings_are_refused(self):
        # CC-CEDICT is published CRLF. A file that lost them is a file that has
        # been through something.
        self._rejects(cedict(eol="\n"), "CRLF")

    def test_a_file_with_no_metadata_header_is_refused(self):
        self._rejects(cedict(fields=False))

    def test_a_missing_metadata_field_is_refused(self):
        text = cedict()
        self._rejects(text.replace("#! subversion=0\r\n", ""), "subversion")

    def test_another_format_is_refused(self):
        self._rejects(cedict(fmt="tt"), "format")

    def test_another_charset_is_refused(self):
        self._rejects(cedict(charset="GB2312"), "charset")

    def test_a_lowercase_charset_is_still_utf8(self):
        assets.verify(self._written(cedict(charset="utf-8")))

    def test_a_non_numeric_entry_count_is_refused(self):
        self._rejects(cedict(declared="many"), "entries")

    def test_a_count_that_disagrees_with_the_body_is_refused(self):
        self._rejects(cedict(declared=len(ENTRIES) + 1), "declares")

    def test_a_truncated_body_is_refused(self):
        text = cedict()
        self._rejects(text[: len(text) - 40])

    def test_a_body_line_that_is_not_an_entry_is_refused(self):
        self._rejects(cedict(entries=ENTRIES + ["not an entry at all"]))

    def test_an_entry_with_no_gloss_is_refused(self):
        self._rejects(cedict(entries=["一 一 [yi1] "]))

    def test_an_entry_with_no_pinyin_bracket_is_refused(self):
        self._rejects(cedict(entries=["一 一 yi1 /one/"]))

    def test_a_comment_after_the_body_starts_is_refused(self):
        self._rejects(cedict(entries=ENTRIES + ["# trailing note"]))

    def test_too_few_entries_is_refused(self):
        with mock.patch.object(assets, "MIN_ENTRIES", 10):
            self._rejects(cedict(), "entries")

    def test_a_missing_file_is_refused_rather_than_raising_oserror(self):
        with self.assertRaises(assets.AssetRejected):
            assets.verify(self.tmp / "absent.txt")


class UpstreamVersionTests(unittest.TestCase):
    """Upstream stamps its own release into the header comments."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _written(self, text: str) -> Path:
        path = self.tmp / "cedict.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_version_is_read_from_the_header(self):
        self.assertEqual(assets.upstream_version(self._written(cedict())), DATE)

    def test_a_file_without_the_header_field_reads_as_empty(self):
        self.assertEqual(
            assets.upstream_version(self._written(cedict(fields=False))), "")

    def test_a_missing_file_reads_as_empty_rather_than_raising(self):
        self.assertEqual(assets.upstream_version(self.tmp / "absent.txt"), "")


class EnsureDictionaryTests(unittest.TestCase):
    """Nothing here touches the network: every fetcher is injected."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.cache = Path(self._tmp.name)
        self.calls = []
        self.notices = []
        self.addCleanup(self._tmp.cleanup)
        patch = mock.patch.object(assets, "MIN_ENTRIES", 1)
        patch.start()
        self.addCleanup(patch.stop)

    def _writing_fetcher(self, content=None):
        content = cedict() if content is None else content

        def fetcher(url, destination):
            self.calls.append((url, destination))
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        return fetcher

    def _ensure(self, fetcher=None):
        return assets.ensure_dictionary(
            self.cache, fetcher or self._writing_fetcher(),
            notice=self.notices.append)

    def _age_the_cache(self, days: float) -> None:
        marker = assets.dictionary_path(self.cache).parent / assets.COMPLETE_MARKER
        old = time.time() - days * 86400
        os.utime(marker, (old, old))

    def test_the_pending_name_cannot_be_worked_out_in_advance(self):
        # A name derived from the pid is a name another process can plant a
        # link at before the download starts. Two attempts must land at two
        # names, and neither may carry the pid.
        def refusing_fetcher(url, destination):
            self.calls.append((url, destination))
            raise assets.AssetRejected("not today")

        self._ensure(refusing_fetcher)
        self._ensure(refusing_fetcher)
        names = [Path(destination).name for _, destination in self.calls]
        self.assertEqual(len(names), 2)
        self.assertNotEqual(names[0], names[1])
        for name in names:
            self.assertNotIn(str(os.getpid()), name)
            self.assertTrue(name.endswith(".part"), name)
            self.assertEqual(Path(self.calls[0][1]).parent,
                             assets.dictionary_path(self.cache).parent)

    def test_a_download_lands_at_the_resolved_path(self):
        path = self._ensure()
        self.assertEqual(path, assets.dictionary_path(self.cache))
        self.assertTrue(path.is_file())

    def test_the_fetcher_is_given_the_upstream_url(self):
        self._ensure()
        url, destination = self.calls[0]
        self.assertEqual(url, assets.dictionary_url())
        # It downloads beside the cached file rather than onto it, so a refresh
        # that fails cannot damage the copy already there.
        self.assertEqual(
            Path(destination).parent, assets.dictionary_path(self.cache).parent
        )
        self.assertNotEqual(Path(destination), assets.dictionary_path(self.cache))

    def test_a_download_marks_the_cache_complete(self):
        self.assertTrue((self._ensure().parent / assets.COMPLETE_MARKER).is_file())

    def test_the_marker_records_the_release_that_was_accepted(self):
        text = cedict()
        path = self._ensure(self._writing_fetcher(text))
        record = json.loads(
            (path.parent / assets.COMPLETE_MARKER).read_text(encoding="utf-8"))
        self.assertEqual(record["date"], DATE)
        self.assertEqual(record["version"], "1")
        self.assertEqual(record["subversion"], "0")
        self.assertEqual(record["entries"], len(ENTRIES))
        self.assertEqual(record["sha256"],
                         hashlib.sha256(text.encode("utf-8")).hexdigest())

    def test_the_recorded_release_is_readable_back(self):
        self._ensure()
        self.assertEqual(assets.cached_release(self.cache)["date"], DATE)

    def test_no_cache_has_no_recorded_release(self):
        self.assertIsNone(assets.cached_release(self.cache))

    def test_the_release_is_summarised_for_a_human(self):
        self._ensure()
        summary = assets.release_summary(assets.cached_release(self.cache))
        self.assertIn(DATE, summary)
        self.assertIn("1.0", summary)
        self.assertIn(str(len(ENTRIES)), summary)
        self.assertIn(hashlib.sha256(cedict().encode("utf-8")).hexdigest()[:12],
                      summary)

    def test_an_absent_release_summarises_rather_than_raising(self):
        self.assertIsInstance(assets.release_summary(None), str)

    def test_a_first_download_says_which_release_arrived(self):
        self._ensure()
        self.assertTrue(any(DATE in line for line in self.notices), self.notices)

    def test_a_refresh_that_brings_a_different_release_says_so(self):
        self._ensure()
        self._age_the_cache(assets.MAX_AGE_DAYS + 1)
        self.notices.clear()
        self._ensure(self._writing_fetcher(cedict(date="2026-09-01T00:00:00Z")))
        said = " ".join(self.notices)
        self.assertIn("2026-09-01T00:00:00Z", said)
        self.assertIn(DATE, said)

    def test_a_refresh_that_brings_the_same_release_is_quiet(self):
        self._ensure()
        self._age_the_cache(assets.MAX_AGE_DAYS + 1)
        self.notices.clear()
        self._ensure()
        self.assertEqual(self.notices, [])

    def test_a_refusal_is_reported_rather_than_swallowed(self):
        self._ensure()
        self._age_the_cache(assets.MAX_AGE_DAYS + 1)
        self.notices.clear()
        self._ensure(self._writing_fetcher("<html>503</html>"))
        self.assertTrue(any("warning" in line for line in self.notices),
                        self.notices)

    def test_a_complete_cache_is_not_downloaded_again(self):
        self._ensure()
        self._ensure()
        self.assertEqual(len(self.calls), 1)

    def test_a_cache_older_than_the_recheck_window_is_downloaded_again(self):
        self._ensure()
        self._age_the_cache(assets.MAX_AGE_DAYS + 1)
        fresh = cedict(entries=["二 二 [er4] /two/"])
        path = self._ensure(self._writing_fetcher(fresh))
        self.assertIn("[er4]", path.read_text(encoding="utf-8"))
        self.assertEqual(len(self.calls), 2)

    def test_a_failed_recheck_keeps_the_dictionary_already_on_disk(self):
        def failing(url, destination):
            raise OSError("network down")

        self._ensure()
        before = assets.dictionary_path(self.cache).read_bytes()
        recorded = assets.cached_release(self.cache)
        self._age_the_cache(assets.MAX_AGE_DAYS + 1)
        path = self._ensure(failing)
        self.assertEqual(path, assets.dictionary_path(self.cache))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(assets.cached_release(self.cache), recorded)

    def test_a_rejected_refresh_keeps_the_dictionary_already_on_disk(self):
        self._ensure()
        before = assets.dictionary_path(self.cache).read_bytes()
        recorded = assets.cached_release(self.cache)
        self._age_the_cache(assets.MAX_AGE_DAYS + 1)
        self._ensure(self._writing_fetcher("<html>not a dictionary</html>"))
        self.assertEqual(assets.dictionary_path(self.cache).read_bytes(), before)
        self.assertEqual(assets.cached_release(self.cache), recorded)

    def test_a_rejected_refresh_leaves_no_partial_file_behind(self):
        self._ensure()
        self._age_the_cache(assets.MAX_AGE_DAYS + 1)
        self._ensure(self._writing_fetcher("<html>not a dictionary</html>"))
        leftovers = [p.name for p in assets.dictionary_path(self.cache).parent.iterdir()]
        self.assertEqual(sorted(leftovers),
                         sorted([assets.DICTIONARY["filename"], assets.COMPLETE_MARKER]))

    def test_a_rejected_first_download_returns_none(self):
        self.assertIsNone(self._ensure(self._writing_fetcher("<html>503</html>")))
        self.assertFalse(assets.dictionary_path(self.cache).exists())

    def test_a_failed_download_returns_none_rather_than_raising(self):
        def fetcher(url, destination):
            raise OSError("network down")

        self.assertIsNone(self._ensure(fetcher))

    def test_a_failed_download_leaves_nothing_to_trust(self):
        def fetcher(url, destination):
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("half a file", encoding="utf-8")
            raise OSError("connection reset")

        self._ensure(fetcher)
        self.assertFalse(assets.dictionary_path(self.cache).exists())
        marker = assets.dictionary_path(self.cache).parent / assets.COMPLETE_MARKER
        self.assertFalse(marker.exists())

    def test_a_partial_cache_is_discarded_and_fetched_again(self):
        target = assets.dictionary_path(self.cache)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stale, never marked complete", encoding="utf-8")
        fresh = cedict(entries=["三 三 [san1] /three/"])
        self.assertIn("[san1]", self._ensure(self._writing_fetcher(fresh))
                      .read_text(encoding="utf-8"))

    def test_a_fetcher_that_writes_nothing_is_a_failure(self):
        self.assertIsNone(self._ensure(lambda url, destination: None))

    def test_the_download_and_the_swap_share_a_directory(self):
        # os.replace is atomic only within one filesystem, which is what makes
        # "nothing partial is ever visible" true rather than hopeful.
        target = assets.dictionary_path(self.cache)
        self._ensure()
        _, destination = self.calls[0]
        self.assertEqual(Path(destination).parent, target.parent)


if __name__ == "__main__":
    unittest.main()
