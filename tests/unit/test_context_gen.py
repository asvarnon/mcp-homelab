"""Tests for context generation — output layout, config routing, and migration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools.context_gen import generate_context, list_context_files, _migrate_legacy_layout


# ---------------------------------------------------------------------------
# Minimal scan fixture — just enough for generate_context to run
# ---------------------------------------------------------------------------

MINIMAL_SCAN: dict[str, Any] = {
    "nodes": [
        {
            "name": "testhost",
            "hostname": "testhost",
            "ip": "10.0.0.1",
            "vlan": 10,
            "ssh_enabled": True,
            "docker_enabled": False,
            "description": "Test node",
        }
    ],
    "node_status": {
        "testhost": {"uptime": "1 day"},
    },
    "containers": {},
    "hardware": {},
    "vms": [],
    "firewall": {},
}


# ---------------------------------------------------------------------------
# Phase 1: generated/ subdirectory layout
# ---------------------------------------------------------------------------


class TestGeneratedSubdirectory:
    """Verify that output always lands under generated/."""

    async def test_default_writes_to_generated_subdir(self, tmp_path: Path) -> None:
        """When no context_dir is set, output goes to <base>/generated/."""
        result = await generate_context(MINIMAL_SCAN, output_dir=tmp_path)

        gen_dir = tmp_path / "generated"
        assert gen_dir.is_dir()
        assert (gen_dir / "infrastructure.md").exists()
        assert (gen_dir / "network.md").exists()
        assert (gen_dir / "nodes" / "testhost.md").exists()
        assert (gen_dir / "known-issues.md").exists()

    async def test_custom_output_dir_writes_to_generated(self, tmp_path: Path) -> None:
        """When output_dir is provided, generated/ is still used."""
        custom_dir = tmp_path / "my-docs"
        custom_dir.mkdir()

        result = await generate_context(MINIMAL_SCAN, output_dir=custom_dir)

        gen_dir = custom_dir / "generated"
        assert gen_dir.is_dir()
        assert (gen_dir / "infrastructure.md").exists()
        assert (gen_dir / "network.md").exists()

    async def test_user_files_untouched(self, tmp_path: Path) -> None:
        """Files outside generated/ are never modified."""
        user_doc = tmp_path / "my-architecture.md"
        user_doc.write_text("# My Architecture\nDon't touch this.\n")

        await generate_context(MINIMAL_SCAN, output_dir=tmp_path)

        assert user_doc.read_text() == "# My Architecture\nDon't touch this.\n"

    async def test_context_dir_in_result(self, tmp_path: Path) -> None:
        """Result dict includes the resolved context_dir."""
        result = await generate_context(MINIMAL_SCAN, output_dir=tmp_path)
        assert result["context_dir"] == str(tmp_path)

    async def test_files_created_are_relative(self, tmp_path: Path) -> None:
        """All paths in files_created are relative to base_dir."""
        result = await generate_context(MINIMAL_SCAN, output_dir=tmp_path)
        for path in result["files_created"]:
            assert not Path(path).is_absolute(), f"Expected relative path, got: {path}"
            assert path.startswith("generated")

    async def test_archive_inside_generated(self, tmp_path: Path) -> None:
        """Archived files live under generated/archived/."""
        # First run creates files
        await generate_context(MINIMAL_SCAN, output_dir=tmp_path)
        # Second run archives them
        result = await generate_context(MINIMAL_SCAN, output_dir=tmp_path)

        assert len(result["files_archived"]) > 0
        for path in result["files_archived"]:
            normalized = path.replace("\\", "/")
            assert "generated/archived/" in normalized, f"Archive not in generated/archived/: {path}"

    async def test_known_issues_created_once(self, tmp_path: Path) -> None:
        """known-issues.md is created on first run, not overwritten on second."""
        await generate_context(MINIMAL_SCAN, output_dir=tmp_path)

        ki_path = tmp_path / "generated" / "known-issues.md"
        ki_path.write_text("# User edited this\n")

        await generate_context(MINIMAL_SCAN, output_dir=tmp_path)
        assert ki_path.read_text() == "# User edited this\n"

    async def test_empty_scan_raises(self, tmp_path: Path) -> None:
        """Scan with no nodes raises RuntimeError."""
        with pytest.raises(RuntimeError, match="no nodes"):
            await generate_context({"nodes": []}, output_dir=tmp_path)


# ---------------------------------------------------------------------------
# Migration from legacy flat layout
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    """Verify one-time cleanup of the old flat context/ layout."""

    def test_migrates_known_issues(self, tmp_path: Path) -> None:
        """known-issues.md is moved into generated/."""
        gen_dir = tmp_path / "generated"
        gen_dir.mkdir()

        ki = tmp_path / "known-issues.md"
        ki.write_text("# My tracked issues\n")

        _migrate_legacy_layout(tmp_path, gen_dir)

        assert not ki.exists(), "Legacy known-issues.md should be removed"
        assert (gen_dir / "known-issues.md").read_text() == "# My tracked issues\n"

    def test_deletes_stale_generated_files(self, tmp_path: Path) -> None:
        """Old infrastructure.md and network.md are deleted."""
        gen_dir = tmp_path / "generated"
        gen_dir.mkdir()

        (tmp_path / "infrastructure.md").write_text("stale")
        (tmp_path / "network.md").write_text("stale")

        _migrate_legacy_layout(tmp_path, gen_dir)

        assert not (tmp_path / "infrastructure.md").exists()
        assert not (tmp_path / "network.md").exists()

    def test_deletes_stale_directories(self, tmp_path: Path) -> None:
        """Old nodes/ and archived/ directories are removed."""
        gen_dir = tmp_path / "generated"
        gen_dir.mkdir()

        nodes_dir = tmp_path / "nodes"
        nodes_dir.mkdir()
        (nodes_dir / "gamehost.md").write_text("stale node")

        archived_dir = tmp_path / "archived"
        archived_dir.mkdir()
        (archived_dir / "old.md").write_text("old archive")

        _migrate_legacy_layout(tmp_path, gen_dir)

        assert not nodes_dir.exists()
        assert not archived_dir.exists()

    def test_skips_when_generated_has_content(self, tmp_path: Path) -> None:
        """Migration is skipped if generated/ already has files."""
        gen_dir = tmp_path / "generated"
        gen_dir.mkdir()
        (gen_dir / "infrastructure.md").write_text("already migrated")

        # Legacy file that should NOT be touched
        legacy = tmp_path / "infrastructure.md"
        legacy.write_text("stale but should survive")

        _migrate_legacy_layout(tmp_path, gen_dir)

        assert legacy.exists(), "Should not delete legacy files when generated/ already populated"

    def test_noop_when_no_legacy_files(self, tmp_path: Path) -> None:
        """No error when there's nothing to migrate."""
        gen_dir = tmp_path / "generated"
        gen_dir.mkdir()

        _migrate_legacy_layout(tmp_path, gen_dir)
        # Just verify no exception — nothing to assert

    def test_preserves_user_files(self, tmp_path: Path) -> None:
        """User files outside the known legacy set are not touched."""
        gen_dir = tmp_path / "generated"
        gen_dir.mkdir()

        user_doc = tmp_path / "my-custom-doc.md"
        user_doc.write_text("important user content")

        # Trigger migration by having a legacy file present
        (tmp_path / "infrastructure.md").write_text("stale")

        _migrate_legacy_layout(tmp_path, gen_dir)

        assert user_doc.read_text() == "important user content"

    async def test_migration_through_generate_context(self, tmp_path: Path) -> None:
        """Integration: generate_context() with legacy files triggers migration.

        This verifies the real code path — not just _migrate_legacy_layout
        called directly. Catches ordering bugs where mkdir runs before migration.
        """
        # Set up legacy flat layout (as if previous version wrote here)
        (tmp_path / "infrastructure.md").write_text("stale infra")
        (tmp_path / "network.md").write_text("stale network")
        ki = tmp_path / "known-issues.md"
        ki.write_text("# User issues\nReal content here.\n")
        nodes_dir = tmp_path / "nodes"
        nodes_dir.mkdir()
        (nodes_dir / "oldhost.md").write_text("stale node")

        # Run generate_context — should migrate then generate fresh
        result = await generate_context(MINIMAL_SCAN, output_dir=tmp_path)

        # Legacy files must be gone
        assert not (tmp_path / "infrastructure.md").exists(), "Legacy infrastructure.md not cleaned up"
        assert not (tmp_path / "network.md").exists(), "Legacy network.md not cleaned up"
        assert not nodes_dir.exists(), "Legacy nodes/ directory not cleaned up"

        # known-issues.md migrated into generated/
        assert not ki.exists(), "Legacy known-issues.md not moved"
        migrated_ki = tmp_path / "generated" / "known-issues.md"
        assert migrated_ki.exists(), "known-issues.md not found in generated/"
        assert "User issues" in migrated_ki.read_text()

        # Fresh generated files exist
        assert (tmp_path / "generated" / "infrastructure.md").exists()
        assert (tmp_path / "generated" / "network.md").exists()


# ---------------------------------------------------------------------------
# list_context_files
# ---------------------------------------------------------------------------


class TestListContextFiles:
    """Verify context file discovery tool."""

    async def test_lists_generated_files(self, tmp_path: Path) -> None:
        """Returns manifest of all files in context dir."""
        # Generate some context first
        await generate_context(MINIMAL_SCAN, output_dir=tmp_path)

        result = await list_context_files(context_dir=tmp_path)

        assert result["context_dir"] == str(tmp_path)
        assert result["total_files"] > 0
        paths = [f["path"] for f in result["files"]]
        # Should find generated files
        normalized = [p.replace("\\", "/") for p in paths]
        assert any("generated/infrastructure.md" in p for p in normalized)
        assert any("generated/network.md" in p for p in normalized)

    async def test_includes_user_files(self, tmp_path: Path) -> None:
        """User docs alongside generated/ are included in manifest."""
        (tmp_path / "my-notes.md").write_text("user content")
        gen_dir = tmp_path / "generated"
        gen_dir.mkdir()
        (gen_dir / "infrastructure.md").write_text("generated")

        result = await list_context_files(context_dir=tmp_path)

        paths = [f["path"] for f in result["files"]]
        normalized = [p.replace("\\", "/") for p in paths]
        assert "my-notes.md" in normalized
        assert "generated/infrastructure.md" in normalized

    async def test_returns_file_metadata(self, tmp_path: Path) -> None:
        """Each file entry has path, size_kb, and modified."""
        (tmp_path / "test.md").write_text("hello world")

        result = await list_context_files(context_dir=tmp_path)

        assert result["total_files"] == 1
        entry = result["files"][0]
        assert "path" in entry
        assert "size_kb" in entry
        assert "modified" in entry
        assert isinstance(entry["size_kb"], float)

    async def test_missing_directory(self, tmp_path: Path) -> None:
        """Returns empty manifest with note when dir doesn't exist."""
        missing = tmp_path / "nonexistent"

        result = await list_context_files(context_dir=missing)

        assert result["total_files"] == 0
        assert result["files"] == []
        assert "note" in result

    async def test_empty_directory(self, tmp_path: Path) -> None:
        """Returns empty manifest for empty dir (no note)."""
        result = await list_context_files(context_dir=tmp_path)

        assert result["total_files"] == 0
        assert result["files"] == []
        assert "note" not in result
