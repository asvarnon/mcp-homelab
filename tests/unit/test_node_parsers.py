"""Unit tests for tools/nodes.py parser functions.

These test the pure parsing logic that transforms SSH command output
into structured data. No SSH connections or mocking needed — just
input strings and expected output dicts.

Think of these like testing a DAO's result-set mapper in Java.
"""

from __future__ import annotations

import pytest

# Import the private parser functions directly for unit testing.
# In Java terms: testing package-private helpers via same-package test class.
from tools.nodes import (
    _extract_label,
    _parse_bsd_cpu_info,
    _parse_bsd_cpu_percent,
    _parse_bsd_disk_gb,
    _parse_bsd_disks,
    _parse_bsd_memory_mb,
    _parse_bsd_physmem,
    _parse_bsd_uptime,
    _parse_cpu_percent,
    _parse_disk_gb,
    _parse_dmidecode,
    _parse_docker_ps,
    _parse_lsblk,
    _parse_lscpu,
    _parse_meminfo,
    _parse_memory_mb,
    _parse_uptime,
    _parse_virt,
    _round_to_consumer_gb,
    _sanitize_container_name,
)


# ===========================================================================
# _parse_uptime
# ===========================================================================


class TestParseUptime:
    def test_standard_output(self) -> None:
        lines = ["up 3 days, 2 hours, 15 minutes"]
        assert _parse_uptime(lines) == "up 3 days, 2 hours, 15 minutes"

    def test_just_hours(self) -> None:
        lines = ["up 5 hours, 30 minutes"]
        assert _parse_uptime(lines) == "up 5 hours, 30 minutes"

    def test_empty_input(self) -> None:
        assert _parse_uptime([]) == "unknown"

    def test_no_match(self) -> None:
        assert _parse_uptime(["not an uptime line"]) == "unknown"

    def test_multi_line_picks_first(self) -> None:
        lines = ["some header", "up 1 day", "up 2 days"]
        assert _parse_uptime(lines) == "up 1 day"


# ===========================================================================
# _parse_cpu_percent
# ===========================================================================


class TestParseCpuPercent:
    def test_standard_idle(self) -> None:
        """95.3% idle → 4.7% used."""
        lines = ["%Cpu(s):  2.1 us,  1.3 sy,  0.0 ni, 95.3 id,  0.0 wa"]
        assert _parse_cpu_percent(lines) == 4.7

    def test_high_usage(self) -> None:
        """10.0% idle → 90.0% used."""
        lines = ["%Cpu(s): 80.0 us, 10.0 sy,  0.0 ni, 10.0 id"]
        assert _parse_cpu_percent(lines) == 90.0

    def test_zero_idle(self) -> None:
        """0% idle → 100% used."""
        lines = ["%Cpu(s): 90.0 us, 10.0 sy,  0.0 ni,  0.0 id"]
        assert _parse_cpu_percent(lines) == 100.0

    def test_integer_idle(self) -> None:
        """Works with integer idle values (no decimal)."""
        lines = ["%Cpu(s):  1.0 us,  0.0 sy,  0.0 ni, 99 id"]
        assert _parse_cpu_percent(lines) == 1.0

    def test_empty_input(self) -> None:
        assert _parse_cpu_percent([]) == 0.0

    def test_no_match(self) -> None:
        assert _parse_cpu_percent(["no cpu data here"]) == 0.0


# ===========================================================================
# _parse_memory_mb
# ===========================================================================


class TestParseMemoryMb:
    def test_standard_output(self) -> None:
        lines = ["Mem:          15904        8123        2456"]
        used, total = _parse_memory_mb(lines)
        assert total == 15904
        assert used == 8123

    def test_empty_input(self) -> None:
        assert _parse_memory_mb([]) == (0, 0)

    def test_no_mem_line(self) -> None:
        assert _parse_memory_mb(["Swap:   4096  0  4096"]) == (0, 0)

    def test_multi_line(self) -> None:
        """Only picks up the Mem: line, ignores Swap."""
        lines = [
            "              total        used        free",
            "Mem:          32000       16000       10000",
            "Swap:          4096           0        4096",
        ]
        used, total = _parse_memory_mb(lines)
        assert total == 32000
        assert used == 16000


# ===========================================================================
# _parse_disk_gb
# ===========================================================================


class TestParseDiskGb:
    def test_real_filesystem(self) -> None:
        lines = [
            "Filesystem      1G-blocks  Used Available Use% Mounted on",
            "/dev/sda1             100G   40G       55G  42% /",
        ]
        result = _parse_disk_gb(lines)
        assert len(result) == 1
        assert result[0]["filesystem"] == "/dev/sda1"
        assert result[0]["total_gb"] == 100
        assert result[0]["used_gb"] == 40
        assert result[0]["available_gb"] == 55
        assert result[0]["use_percent"] == "42%"
        assert result[0]["mount"] == "/"

    def test_skips_tmpfs(self) -> None:
        lines = [
            "tmpfs               8G    0G     8G   0% /dev/shm",
            "/dev/sda1         100G   40G    55G  42% /",
        ]
        result = _parse_disk_gb(lines)
        assert len(result) == 1
        assert result[0]["filesystem"] == "/dev/sda1"

    def test_skips_efivarfs(self) -> None:
        lines = ["efivarfs            1G    0G     1G   0% /sys/firmware/efi/efivars"]
        assert _parse_disk_gb(lines) == []

    def test_skips_devtmpfs(self) -> None:
        lines = ["devtmpfs            8G    0G     8G   0% /dev"]
        assert _parse_disk_gb(lines) == []

    def test_skips_overlay(self) -> None:
        lines = ["overlay            50G   10G    35G  22% /var/lib/docker/overlay2/merged"]
        assert _parse_disk_gb(lines) == []

    def test_skips_header(self) -> None:
        lines = ["Filesystem      1G-blocks  Used Available Use% Mounted on"]
        assert _parse_disk_gb(lines) == []

    def test_empty_input(self) -> None:
        assert _parse_disk_gb([]) == []

    def test_multiple_real_filesystems(self) -> None:
        lines = [
            "/dev/sda1         100G   40G    55G  42% /",
            "/dev/sdb1         500G  200G   280G  42% /data",
        ]
        result = _parse_disk_gb(lines)
        assert len(result) == 2


# ===========================================================================
# _parse_docker_ps
# ===========================================================================


class TestParseDockerPs:
    def test_single_container(self) -> None:
        raw = '{"Names":"web","Image":"nginx:latest","Status":"Up 3 days","Ports":"80/tcp","Labels":""}'
        result = _parse_docker_ps(raw)
        assert len(result) == 1
        assert result[0]["name"] == "web"
        assert result[0]["image"] == "nginx:latest"
        assert result[0]["status"] == "Up 3 days"
        assert result[0]["ports"] == "80/tcp"

    def test_multiple_containers(self) -> None:
        raw = (
            '{"Names":"web","Image":"nginx","Status":"Up","Ports":"","Labels":""}\n'
            '{"Names":"db","Image":"postgres","Status":"Up","Ports":"5432/tcp","Labels":""}'
        )
        result = _parse_docker_ps(raw)
        assert len(result) == 2
        assert result[0]["name"] == "web"
        assert result[1]["name"] == "db"

    def test_with_oci_labels(self) -> None:
        labels = "org.opencontainers.image.title=MyApp,com.docker.compose.service=app"
        raw = f'{{"Names":"app","Image":"myapp:1.0","Status":"Up","Ports":"","Labels":"{labels}"}}'
        result = _parse_docker_ps(raw)
        assert result[0]["image_title"] == "MyApp"
        assert result[0]["compose_service"] == "app"

    def test_empty_input(self) -> None:
        assert _parse_docker_ps("") == []

    def test_blank_lines_ignored(self) -> None:
        raw = '\n{"Names":"x","Image":"y","Status":"Up","Ports":"","Labels":""}\n\n'
        result = _parse_docker_ps(raw)
        assert len(result) == 1


# ===========================================================================
# _extract_label
# ===========================================================================


class TestExtractLabel:
    def test_found(self) -> None:
        labels = "org.opencontainers.image.title=MyApp,version=1.0"
        assert _extract_label(labels, "org.opencontainers.image.title") == "MyApp"

    def test_not_found(self) -> None:
        assert _extract_label("a=1,b=2", "c") == ""

    def test_empty_labels(self) -> None:
        assert _extract_label("", "anything") == ""


# ===========================================================================
# _sanitize_container_name
# ===========================================================================


class TestSanitizeContainerName:
    def test_clean_name(self) -> None:
        assert _sanitize_container_name("my-container_v1.2") == "my-container_v1.2"

    def test_strips_slashes(self) -> None:
        assert _sanitize_container_name("/my-container") == "my-container"

    def test_strips_special_chars(self) -> None:
        assert _sanitize_container_name("my container!@#") == "mycontainer"

    def test_strips_semicolons(self) -> None:
        """Ensures command injection via container name is impossible."""
        assert _sanitize_container_name("web; rm -rf /") == "webrm-rf"


# ===========================================================================
# _parse_lscpu
# ===========================================================================


class TestParseLscpu:
    def test_standard_output(self) -> None:
        lines = [
            "Architecture:          x86_64",
            "CPU(s):                8",
            "Socket(s):             1",
            "Model name:            Intel(R) Core(TM) i7-10700",
        ]
        result = _parse_lscpu(lines)
        assert result["cpu_model"] == "Intel(R) Core(TM) i7-10700"
        assert result["cpu_cores"] == 8
        assert result["cpu_sockets"] == 1
        assert result["architecture"] == "x86_64"

    def test_missing_fields(self) -> None:
        result = _parse_lscpu([])
        assert result["cpu_model"] == "unknown"
        assert result["cpu_cores"] == 0
        assert result["cpu_sockets"] == 0
        assert result["architecture"] == "unknown"

    def test_non_digit_cores(self) -> None:
        """Edge case: CPU(s) field has non-numeric value."""
        lines = ["CPU(s):                N/A"]
        result = _parse_lscpu(lines)
        assert result["cpu_cores"] == 0


# ===========================================================================
# _parse_meminfo
# ===========================================================================


class TestParseMeminfo:
    def test_standard_output(self) -> None:
        lines = [
            "MemTotal:       16310796 kB",
            "MemFree:         1234567 kB",
        ]
        # 16310796 // 1024 = 15928
        assert _parse_meminfo(lines) == 15928

    def test_empty_input(self) -> None:
        assert _parse_meminfo([]) == 0

    def test_no_memtotal(self) -> None:
        assert _parse_meminfo(["MemFree: 1000 kB"]) == 0


# ===========================================================================
# _parse_lsblk
# ===========================================================================


class TestParseLsblk:
    def test_single_disk(self) -> None:
        lines = ["sda    500G disk Samsung SSD 870"]
        result = _parse_lsblk(lines)
        assert len(result) == 1
        assert result[0]["name"] == "sda"
        assert result[0]["size"] == "500G"
        assert result[0]["model"] == "Samsung SSD 870"

    def test_skips_partitions(self) -> None:
        lines = [
            "sda    500G disk Samsung SSD",
            "sda1   100G part",
        ]
        result = _parse_lsblk(lines)
        assert len(result) == 1
        assert result[0]["name"] == "sda"

    def test_no_model(self) -> None:
        lines = ["sda 500G disk"]
        result = _parse_lsblk(lines)
        assert result[0]["model"] == ""

    def test_empty_input(self) -> None:
        assert _parse_lsblk([]) == []


# ===========================================================================
# _parse_virt
# ===========================================================================


class TestParseVirt:
    def test_none(self) -> None:
        assert _parse_virt(["none"]) == "none"

    def test_kvm(self) -> None:
        assert _parse_virt(["kvm"]) == "kvm"

    def test_empty(self) -> None:
        assert _parse_virt([]) == "unknown"

    def test_whitespace(self) -> None:
        assert _parse_virt(["  qemu  "]) == "qemu"


# ===========================================================================
# _parse_dmidecode
# ===========================================================================


class TestParseDmidecode:
    def test_populated_slot(self) -> None:
        lines = [
            "Memory Device",
            "\tSize: 16 GB",
            "\tType: DDR4",
            "\tSpeed: 3200 MT/s",
            "\tManufacturer: Crucial",
            "\tForm Factor: DIMM",
            "\tLocator: DIMM_A1",
        ]
        result = _parse_dmidecode(lines)
        assert len(result) == 1
        assert result[0]["size"] == "16 GB"
        assert result[0]["type"] == "DDR4"
        assert result[0]["speed"] == "3200 MT/s"
        assert result[0]["manufacturer"] == "Crucial"

    def test_filters_empty_slots(self) -> None:
        lines = [
            "Memory Device",
            "\tSize: 16 GB",
            "\tType: DDR4",
            "Memory Device",
            "\tSize: No Module Installed",
            "\tType: Unknown",
        ]
        result = _parse_dmidecode(lines)
        assert len(result) == 1
        assert result[0]["size"] == "16 GB"

    def test_empty_input(self) -> None:
        assert _parse_dmidecode([]) == []

    def test_multiple_populated_slots(self) -> None:
        lines = [
            "Memory Device",
            "\tSize: 16 GB",
            "\tType: DDR4",
            "Memory Device",
            "\tSize: 16 GB",
            "\tType: DDR4",
        ]
        result = _parse_dmidecode(lines)
        assert len(result) == 2

    def test_filters_not_installed(self) -> None:
        """Also handles 'Not Installed' variant."""
        lines = [
            "Memory Device",
            "\tSize: Not Installed",
        ]
        assert _parse_dmidecode(lines) == []


# ===========================================================================
# _round_to_consumer_gb
# ===========================================================================


class TestRoundToConsumerGb:
    def test_exact_16gb(self) -> None:
        # 16384 MB = exactly 16 GB
        assert _round_to_consumer_gb(16384) == 16

    def test_slightly_under_16gb(self) -> None:
        # Kernel reserves some RAM; 15904 MB ≈ 15.5 GB → rounds to 16 GB
        assert _round_to_consumer_gb(15904) == 16

    def test_32gb(self) -> None:
        assert _round_to_consumer_gb(32000) == 32

    def test_8gb(self) -> None:
        assert _round_to_consumer_gb(8000) == 8

    def test_4gb(self) -> None:
        assert _round_to_consumer_gb(4000) == 4

    def test_64gb(self) -> None:
        assert _round_to_consumer_gb(64000) == 64

    def test_zero(self) -> None:
        assert _round_to_consumer_gb(0) == 0

    def test_negative(self) -> None:
        assert _round_to_consumer_gb(-1) == 0


# ===========================================================================
# FreeBSD parsers
# ===========================================================================


class TestParseBsdUptime:
    def test_days_and_hours(self) -> None:
        lines = [" 3:45PM  up 11 days,  2:15, 1 user, load averages: 0.15, 0.10, 0.08"]
        assert _parse_bsd_uptime(lines) == "up 11 days, 2:15"

    def test_hours_only(self) -> None:
        lines = ["10:00AM  up  5:30, 2 users, load averages: 0.01, 0.02, 0.00"]
        assert _parse_bsd_uptime(lines) == "up 5:30"

    def test_single_day(self) -> None:
        lines = [" 1:00PM  up 1 day,  0:45, 1 user, load averages: 0.00, 0.00, 0.00"]
        assert _parse_bsd_uptime(lines) == "up 1 day, 0:45"

    def test_empty_input(self) -> None:
        assert _parse_bsd_uptime([]) == "unknown"

    def test_no_match(self) -> None:
        assert _parse_bsd_uptime(["not uptime output"]) == "unknown"


class TestParseBsdCpuPercent:
    def test_standard_vmstat(self) -> None:
        """Last three columns: us=2, sy=1, id=97 → 3.0% used."""
        lines = [
            " procs      memory      page                    disks     faults         cpu",
            " r b w     avm    fre   flt  re  pi  po    fr  sr da0   in   sy   cs us sy id",
            " 0 0 0  123456 789012     0   0   0   0     0   0   0    3   12   15  1  0 99",
            " 0 0 0  123456 789012     0   0   0   0     0   0   0    5   20   25  2  1 97",
        ]
        assert _parse_bsd_cpu_percent(lines) == 3.0

    def test_high_usage(self) -> None:
        lines = [
            " r b w     avm    fre",
            " 0 0 0  123456 789012     0   0   0   0     0   0   0    5   20   25 40 10 50",
        ]
        assert _parse_bsd_cpu_percent(lines) == 50.0

    def test_empty_input(self) -> None:
        assert _parse_bsd_cpu_percent([]) == 0.0

    def test_no_data_lines(self) -> None:
        lines = [" procs memory page", " r b w avm fre"]
        assert _parse_bsd_cpu_percent(lines) == 0.0


class TestParseBsdMemoryMb:
    def test_standard_sysctl(self) -> None:
        """16 GB physmem, some free pages, 4K page size."""
        physmem = 16803807232  # ~16 GB
        free_pages = 2000000
        page_size = 4096
        lines = [str(physmem), str(free_pages), str(page_size)]
        used, total = _parse_bsd_memory_mb(lines)
        assert total == physmem // (1024 * 1024)
        expected_free = (free_pages * page_size) // (1024 * 1024)
        assert used == total - expected_free

    def test_physmem_only_fallback(self) -> None:
        """If only physmem is available, used=0."""
        lines = ["16803807232"]
        used, total = _parse_bsd_memory_mb(lines)
        assert total == 16803807232 // (1024 * 1024)
        assert used == 0

    def test_empty_input(self) -> None:
        assert _parse_bsd_memory_mb([]) == (0, 0)


class TestParseBsdDiskGb:
    def test_standard_freebsd_df(self) -> None:
        lines = [
            "Filesystem  1G-blocks  Used  Avail Capacity  Mounted on",
            "/dev/gpt/rootfs     100    20     75       21%    /",
        ]
        result = _parse_bsd_disk_gb(lines)
        assert len(result) == 1
        assert result[0]["filesystem"] == "/dev/gpt/rootfs"
        assert result[0]["total_gb"] == 100
        assert result[0]["used_gb"] == 20
        assert result[0]["available_gb"] == 75
        assert result[0]["use_percent"] == "21%"
        assert result[0]["mount"] == "/"

    def test_skips_devfs(self) -> None:
        lines = [
            "devfs               0    0     0   100%    /dev",
            "/dev/ada0p2       100   20    75    21%    /",
        ]
        result = _parse_bsd_disk_gb(lines)
        assert len(result) == 1
        assert result[0]["filesystem"] == "/dev/ada0p2"

    def test_skips_tmpfs(self) -> None:
        lines = ["tmpfs      8    0     8   0%    /tmp"]
        assert _parse_bsd_disk_gb(lines) == []

    def test_empty_input(self) -> None:
        assert _parse_bsd_disk_gb([]) == []


class TestParseBsdCpuInfo:
    def test_standard_sysctl(self) -> None:
        lines = [
            "Intel(R) Celeron(R) J4125 CPU @ 2.00GHz",
            "4",
            "amd64",
        ]
        result = _parse_bsd_cpu_info(lines)
        assert result["cpu_model"] == "Intel(R) Celeron(R) J4125 CPU @ 2.00GHz"
        assert result["cpu_cores"] == 4
        assert result["cpu_sockets"] == 1
        assert result["architecture"] == "amd64"

    def test_empty_input(self) -> None:
        result = _parse_bsd_cpu_info([])
        assert result["cpu_model"] == "unknown"
        assert result["cpu_cores"] == 0
        assert result["architecture"] == "unknown"


class TestParseBsdPhysmem:
    def test_standard_output(self) -> None:
        lines = ["16803807232"]
        assert _parse_bsd_physmem(lines) == 16803807232 // (1024 * 1024)

    def test_empty_input(self) -> None:
        assert _parse_bsd_physmem([]) == 0


class TestParseBsdDisks:
    def test_single_line(self) -> None:
        lines = ["ada0 ada1 nvd0"]
        result = _parse_bsd_disks(lines)
        assert len(result) == 3
        assert result[0]["name"] == "ada0"
        assert result[1]["name"] == "ada1"
        assert result[2]["name"] == "nvd0"

    def test_empty_input(self) -> None:
        assert _parse_bsd_disks([]) == []

    def test_blank_lines(self) -> None:
        lines = ["", "ada0", ""]
        result = _parse_bsd_disks(lines)
        assert len(result) == 1
        assert result[0]["name"] == "ada0"


# ===========================================================================
# FreeBSD dispatch integration tests
# ===========================================================================


class TestGetNodeStatusFreebsd:
    """Verify get_node_status() takes the FreeBSD path when os='freebsd'."""

    @pytest.fixture(autouse=True)
    def _stub_ssh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub _get_host_os and _compound_ssh_query for FreeBSD path."""
        from tools import nodes

        monkeypatch.setattr(nodes, "_get_host_os", lambda _: "freebsd")

        # Four sections matching: uptime, vmstat, sysctl mem, df -g
        async def fake_query(hostname: str, commands: list[str], **kw: object) -> list[list[str]]:
            return [
                [" 3:45PM  up 2 days,  1:30, 1 user, load averages: 0.10, 0.05, 0.01"],
                [
                    " procs memory page disks faults cpu",
                    " r b w avm fre",
                    " 0 0 0 100 200  0 0 0 0 0 0 0  5 10 15 3 2 95",
                ],
                ["8589934592", "1000000", "4096"],
                [
                    "Filesystem 1G-blocks Used Avail Capacity Mounted on",
                    "/dev/ada0p2      100   20    75    21%    /",
                ],
            ]

        monkeypatch.setattr(nodes, "_compound_ssh_query", fake_query)

    async def test_returns_bsd_status(self) -> None:
        from tools.nodes import get_node_status

        result = await get_node_status("opnsense")
        assert result["uptime"] == "up 2 days, 1:30"
        assert result["cpu_percent"] == 5.0
        assert result["ram_total_mb"] == 8589934592 // (1024 * 1024)
        assert len(result["filesystems"]) == 1
        assert result["filesystems"][0]["mount"] == "/"


class TestGetHardwareSpecsFreebsd:
    """Verify get_hardware_specs() takes the FreeBSD path when os='freebsd'."""

    @pytest.fixture(autouse=True)
    def _stub_ssh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tools import nodes

        monkeypatch.setattr(nodes, "_get_host_os", lambda _: "freebsd")

        # Four sections: cpu sysctl, physmem, kern.disks, kern.vm_guest
        async def fake_query(hostname: str, commands: list[str], **kw: object) -> list[list[str]]:
            return [
                ["Intel(R) Celeron(R) J4125 CPU @ 2.00GHz", "4", "amd64"],
                ["8589934592"],
                ["ada0 ada1"],
                ["none"],
            ]

        monkeypatch.setattr(nodes, "_compound_ssh_query", fake_query)

    async def test_returns_bsd_specs(self) -> None:
        from tools.nodes import get_hardware_specs

        result = await get_hardware_specs("opnsense")
        assert result["cpu_model"] == "Intel(R) Celeron(R) J4125 CPU @ 2.00GHz"
        assert result["cpu_cores"] == 4
        assert result["architecture"] == "amd64"
        assert result["ram_total_mb"] == 8589934592 // (1024 * 1024)
        assert len(result["disks"]) == 2
        assert result["memory_modules"] == []
        assert result["is_vm"] is False
