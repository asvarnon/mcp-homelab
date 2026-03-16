"""Built-in role templates for SSH user provisioning.

Roles define what permissions a service account gets on a target host:
groups, sudoers rules, and readable paths. Applied during ``mcp-homelab setup ssh``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RoleTemplate:
    """Defines the permission set for a service account on a host."""

    name: str
    description: str
    groups: list[str] = field(default_factory=list)
    sudoers: list[str] = field(default_factory=list)
    read_paths: list[str] = field(default_factory=list)

    def sudoers_lines(self, username: str) -> list[str]:
        """Generate sudoers drop-in lines for the given user.

        Returns:
            List of sudoers rule strings, one per allowed command.
        """
        return [
            f"{username} ALL=(ALL) NOPASSWD: {cmd}"
            for cmd in self.sudoers
        ]

    def sudoers_file_content(self, username: str) -> str:
        """Generate the full sudoers drop-in file content.

        Returns:
            String suitable for writing to ``/etc/sudoers.d/mcp-homelab``.
        """
        lines = [f"# mcp-homelab role: {self.name}"]
        lines.extend(self.sudoers_lines(username))
        lines.append("")  # trailing newline
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Built-in roles
# ---------------------------------------------------------------------------

ROLE_GAMEHOST = RoleTemplate(
    name="gamehost",
    description="Game server host with Docker and script access",
    groups=["docker"],
    sudoers=[
        "/srv/gamehost/scripts/backup.sh",
        "/srv/gamehost/scripts/switch-game.sh",
        "/srv/gamehost/scripts/restore.sh",
        "/usr/bin/systemctl restart docker",
    ],
    read_paths=[
        "/srv/gamehost/",
        "/var/log/",
    ],
)

ROLE_READONLY = RoleTemplate(
    name="readonly",
    description="Observation only — no modifications",
    groups=[],
    sudoers=[],
    read_paths=["/var/log/"],
)

ROLE_DOCKER_HOST = RoleTemplate(
    name="docker-host",
    description="General Docker host with container management",
    groups=["docker"],
    sudoers=[],
    read_paths=["/var/log/"],
)

ROLE_PROXMOX_NODE = RoleTemplate(
    name="proxmox-node",
    description="Proxmox host — SSH for stats, API for VM management",
    groups=[],
    sudoers=[],
    read_paths=["/etc/pve/", "/var/log/"],
)

ROLE_FIREWALL = RoleTemplate(
    name="firewall",
    description="OPNsense / FreeBSD firewall — read-only inspection",
    groups=[],
    sudoers=[],
    read_paths=["/var/log/"],
)


# Lookup table: role name → template
BUILT_IN_ROLES: dict[str, RoleTemplate] = {
    role.name: role
    for role in [
        ROLE_GAMEHOST,
        ROLE_READONLY,
        ROLE_DOCKER_HOST,
        ROLE_PROXMOX_NODE,
        ROLE_FIREWALL,
    ]
}


def get_role(name: str) -> RoleTemplate:
    """Look up a built-in role by name.

    Args:
        name: Role name (e.g. ``"gamehost"``, ``"readonly"``).

    Returns:
        The matching RoleTemplate.

    Raises:
        KeyError: If no built-in role matches.
    """
    if name not in BUILT_IN_ROLES:
        available = ", ".join(sorted(BUILT_IN_ROLES))
        raise KeyError(f"Unknown role: {name!r}. Available: {available}")
    return BUILT_IN_ROLES[name]


def list_roles() -> list[RoleTemplate]:
    """Return all built-in role templates."""
    return list(BUILT_IN_ROLES.values())
