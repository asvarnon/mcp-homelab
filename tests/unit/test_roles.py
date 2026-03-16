"""Unit tests for mcp_homelab/setup/roles.py.

Tests role template data structures, built-in role definitions,
lookup/list helpers, and sudoers generation.
"""

from __future__ import annotations

import pytest

from mcp_homelab.setup.roles import (
    BUILT_IN_ROLES,
    RoleTemplate,
    get_role,
    list_roles,
)


# ===========================================================================
# RoleTemplate — sudoers generation
# ===========================================================================


class TestRoleTemplate:
    """Tests for the RoleTemplate dataclass methods."""

    def test_sudoers_lines_one_command(self) -> None:
        role = RoleTemplate(
            name="test",
            description="test role",
            sudoers=["/usr/bin/systemctl restart docker"],
        )
        lines = role.sudoers_lines("svc")
        assert lines == [
            "svc ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart docker"
        ]

    def test_sudoers_lines_multiple_commands(self) -> None:
        role = RoleTemplate(
            name="test",
            description="test role",
            sudoers=["/usr/bin/cmd1", "/usr/bin/cmd2", "/usr/sbin/cmd3"],
        )
        lines = role.sudoers_lines("mcp-homelab")
        assert len(lines) == 3
        assert all(line.startswith("mcp-homelab ALL=(ALL) NOPASSWD:") for line in lines)

    def test_sudoers_lines_empty(self) -> None:
        role = RoleTemplate(name="noop", description="no sudoers")
        assert role.sudoers_lines("anyuser") == []

    def test_sudoers_file_content_format(self) -> None:
        role = RoleTemplate(
            name="demo",
            description="demo role",
            sudoers=["/usr/bin/docker ps"],
        )
        content = role.sudoers_file_content("svc")
        assert content.startswith("# mcp-homelab role: demo\n")
        assert "svc ALL=(ALL) NOPASSWD: /usr/bin/docker ps" in content
        # Must end with trailing newline
        assert content.endswith("\n")

    def test_sudoers_file_content_empty_sudoers(self) -> None:
        role = RoleTemplate(name="empty", description="empty")
        content = role.sudoers_file_content("user")
        assert "# mcp-homelab role: empty" in content
        # Only header + trailing newline, no rule lines
        lines = content.strip().splitlines()
        assert len(lines) == 1

    def test_frozen_dataclass(self) -> None:
        role = RoleTemplate(name="x", description="x")
        with pytest.raises(AttributeError):
            role.name = "changed"  # type: ignore[misc]


# ===========================================================================
# Built-in roles
# ===========================================================================


class TestBuiltInRoles:
    """Verify all five built-in roles exist and have correct structure."""

    EXPECTED_NAMES = {"gamehost", "readonly", "docker-host", "proxmox-node", "firewall"}

    def test_all_roles_present(self) -> None:
        assert set(BUILT_IN_ROLES.keys()) == self.EXPECTED_NAMES

    def test_gamehost_role(self) -> None:
        role = BUILT_IN_ROLES["gamehost"]
        assert role.name == "gamehost"
        assert "docker" in role.groups
        assert len(role.sudoers) > 0
        assert any("/srv/gamehost/scripts/backup.sh" in s for s in role.sudoers)

    def test_readonly_role(self) -> None:
        role = BUILT_IN_ROLES["readonly"]
        assert role.name == "readonly"
        assert role.groups == []
        assert role.sudoers == []

    def test_docker_host_role(self) -> None:
        role = BUILT_IN_ROLES["docker-host"]
        assert role.name == "docker-host"
        assert "docker" in role.groups
        assert role.sudoers == []

    def test_proxmox_node_role(self) -> None:
        role = BUILT_IN_ROLES["proxmox-node"]
        assert role.name == "proxmox-node"
        assert role.groups == []

    def test_firewall_role(self) -> None:
        role = BUILT_IN_ROLES["firewall"]
        assert role.name == "firewall"
        assert "/var/log/" in role.read_paths

    def test_all_roles_are_role_templates(self) -> None:
        for role in BUILT_IN_ROLES.values():
            assert isinstance(role, RoleTemplate)

    def test_all_roles_have_descriptions(self) -> None:
        for role in BUILT_IN_ROLES.values():
            assert role.description, f"Role {role.name} has empty description"


# ===========================================================================
# get_role
# ===========================================================================


class TestGetRole:
    """Tests for the get_role() lookup function."""

    def test_valid_lookup(self) -> None:
        role = get_role("gamehost")
        assert role.name == "gamehost"

    def test_all_built_in_roles_are_retrievable(self) -> None:
        for name in BUILT_IN_ROLES:
            role = get_role(name)
            assert role.name == name

    def test_invalid_lookup_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="Unknown role"):
            get_role("nonexistent")

    def test_error_message_lists_available_roles(self) -> None:
        with pytest.raises(KeyError, match="Available:"):
            get_role("bogus")


# ===========================================================================
# list_roles
# ===========================================================================


class TestListRoles:
    """Tests for the list_roles() helper."""

    def test_returns_all_five(self) -> None:
        roles = list_roles()
        assert len(roles) == 5

    def test_returns_role_template_instances(self) -> None:
        for role in list_roles():
            assert isinstance(role, RoleTemplate)

    def test_names_match_built_in(self) -> None:
        names = {role.name for role in list_roles()}
        assert names == set(BUILT_IN_ROLES.keys())
