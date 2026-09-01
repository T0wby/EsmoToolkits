"""Offline checks for dump_xml's tolerance of adb chatter. No emulator, no adb."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import esmo_capture as ec  # noqa: E402
import esmo_explore as ee  # noqa: E402

XML = "<?xml version='1.0'?><hierarchy><node content-desc='Expert'/></hierarchy>"

# adb prints this to STDOUT, in front of the payload, whenever it (re)starts the
# server - which happens on any first connect, and on every call when a second adb
# owns the daemon. It is the reason a whole strategy-editor screen once looked like
# it had no accessibility tree at all.
BANNER = ("adb server version (41) doesn't match this client (36); killing...\n"
          "* daemon started successfully *\n")


class FakeAdb:
    """Answers `uiautomator dump` with nothing and `cat` with the given payload."""

    def __init__(self, payload):
        self.payload = payload

    def shell(self, *args, **kwargs):
        return (0, self.payload if args[0] == "cat" else "", "")


def test_banner_before_the_declaration_is_stripped():
    for mod in (ec, ee):
        assert mod.dump_xml(FakeAdb(BANNER + XML)) == XML


def test_clean_output_is_unchanged():
    for mod in (ec, ee):
        assert mod.dump_xml(FakeAdb(XML)) == XML


def test_output_without_any_xml_is_still_a_failure():
    for mod in (ec, ee):
        assert mod.dump_xml(FakeAdb(BANNER)) is None


class FakeShotAdb:
    """Answers `exec-out screencap -p` with the given raw bytes."""

    def __init__(self, payload):
        self.payload = payload

    def run(self, *args, **kwargs):
        return (0, self.payload, b"")


PNG = ec.PNG_MAGIC + b"\rIHDR\nbody\r\n"


def test_banner_before_the_png_is_stripped():
    for mod in (ec, ee):
        assert mod.screencap(FakeShotAdb(BANNER.encode() + PNG)) == PNG


def test_crlf_mangled_png_is_unmangled():
    mangled = PNG.replace(b"\n", b"\r\n")
    for mod in (ec, ee):
        assert mod.screencap(FakeShotAdb(mangled)) == mangled.replace(b"\r\n", b"\n")


def test_output_without_any_png_is_a_failure():
    # Better None than a corrupt blob: the caller crashed in PIL three hours in.
    for mod in (ec, ee):
        assert mod.screencap(FakeShotAdb(BANNER.encode())) is None


def test_server_version_mismatch_is_reported():
    # 5s per adb call instead of 0.01s - every wait times out, the walk is useless.
    assert ec.check_server_owner(
        "adb server version (41) doesn't match this client (36); killing...\n"
        "* daemon started successfully *\n")
    assert not ec.check_server_owner("List of devices attached\nemulator-5554\tdevice\n")
    assert not ec.check_server_owner("")
