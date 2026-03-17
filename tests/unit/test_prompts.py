"""Unit tests for mcp_homelab/setup/prompts.py.

Tests input validation loops and edge cases. Uses monkeypatch to
simulate user input — similar to mocking System.in in Java.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_homelab.setup.prompts import (
    _NODE_NAME_RE,
    prompt_int,
    prompt_int_optional,
    prompt_ip,
    prompt_node_name,
    prompt_path,
    prompt_secret,
    prompt_str,
    prompt_yn,
)


class TestPromptStr:
    def test_accepts_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "hello")
        assert prompt_str("Name") == "hello"

    def test_uses_default_on_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert prompt_str("Name", default="world") == "world"

    def test_retries_on_empty_no_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inputs = iter(["", "", "finally"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        assert prompt_str("Name") == "finally"


class TestPromptIp:
    def test_valid_ipv4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "192.0.2.10")
        assert prompt_ip("IP") == "192.0.2.10"

    def test_valid_ipv6(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "::1")
        assert prompt_ip("IP") == "::1"

    def test_rejects_invalid_then_accepts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inputs = iter(["not.an.ip", "198.51.100.1"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        assert prompt_ip("IP") == "198.51.100.1"

    def test_uses_default_on_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert prompt_ip("IP", default="192.168.1.1") == "192.168.1.1"


class TestPromptInt:
    def test_valid_number(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "42")
        assert prompt_int("VLAN") == 42

    def test_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert prompt_int("Port", default=8006) == 8006

    def test_rejects_non_numeric(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inputs = iter(["abc", "123"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        assert prompt_int("VLAN") == 123


class TestPromptIntOptional:
    def test_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "50")
        assert prompt_int_optional("VLAN") == 50

    def test_returns_none_on_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert prompt_int_optional("VLAN") is None

    def test_returns_none_on_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "abc")
        assert prompt_int_optional("VLAN") is None


class TestPromptPath:
    def test_existing_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        test_file = tmp_path / "id_ed25519"
        test_file.write_text("fake key")
        monkeypatch.setattr("builtins.input", lambda _: str(test_file))
        assert prompt_path("SSH key") == str(test_file)

    def test_retries_on_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        real_file = tmp_path / "real"
        real_file.write_text("exists")
        inputs = iter([str(tmp_path / "missing"), str(real_file)])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        assert prompt_path("SSH key") == str(real_file)


class TestPromptYn:
    def test_yes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "y")
        assert prompt_yn("Enable SSH?") is True

    def test_no(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert prompt_yn("Enable SSH?") is False

    def test_default_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert prompt_yn("Enable SSH?") is False

    def test_default_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert prompt_yn("Enable SSH?", default=True) is True

    def test_yes_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for answer in ("y", "Y", "yes", "YES", "Yes"):
            monkeypatch.setattr("builtins.input", lambda _, a=answer: a)
            assert prompt_yn("Q?") is True


class TestNodeNameRegex:
    """Tests the regex pattern used by prompt_node_name."""

    def test_valid_names(self) -> None:
        for name in ("test-node-1", "test-node-2", "my-server", "node_1", "A1"):
            assert _NODE_NAME_RE.match(name), f"Should accept: {name}"

    def test_rejects_starting_with_number(self) -> None:
        assert not _NODE_NAME_RE.match("1invalid")

    def test_rejects_starting_with_hyphen(self) -> None:
        assert not _NODE_NAME_RE.match("-invalid")

    def test_rejects_spaces(self) -> None:
        assert not _NODE_NAME_RE.match("my server")

    def test_rejects_special_chars(self) -> None:
        assert not _NODE_NAME_RE.match("node@1")


class TestPromptNodeName:
    def test_valid_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "test-node-1")
        assert prompt_node_name("Node name") == "test-node-1"

    def test_rejects_then_accepts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inputs = iter(["1bad", "good-name"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        assert prompt_node_name("Node name") == "good-name"

    def test_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert prompt_node_name("Node name", default="test-node-2") == "test-node-2"


class TestPromptSecret:
    def test_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("getpass.getpass", lambda _: "my-secret")
        assert prompt_secret("Token") == "my-secret"

    def test_retries_on_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = iter(["", "", "finally-a-secret"])
        monkeypatch.setattr("getpass.getpass", lambda _: next(calls))
        assert prompt_secret("Token") == "finally-a-secret"
