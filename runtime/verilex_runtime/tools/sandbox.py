"""macOS Seatbelt sandbox for tool subprocesses.

Wraps a spawned command so it runs under a Codex-style Seatbelt profile applied
via ``sandbox-exec -p``. The profile is closed-by-default (``deny default``),
then explicitly re-opens the system bootstrap the runtime needs (process exec /
fork, system read paths, sysctl, scratch temp dirs) and grants write only to
the authorized workspace root, while denying network egress.

Reference structure follows OpenAI Codex's macOS Seatbelt profile
(``sandboxing/src/seatbelt.rs``), which is modeled on Chromium's sandbox policy.

Security model (mirrors Codex): the sandbox restricts *capability* (which paths
a process can write, whether it can reach the network); it does not allowlist
command names. Read isolation is intentionally broad (``file-read*``) because a
narrower read whitelist crashes the dyld loader on modern macOS (SIGABRT); reads
are instead bounded at the application layer by the file tools' path traversal
checks.

``sandbox-exec`` is present on macOS 12+ but is deprecated by Apple. When it is
absent, contexts requiring protected-file or read-only isolation fail closed.
Fully writable ordinary workspaces keep the existing unsandboxed platform
behavior; this is not equivalent to macOS write/network isolation.
"""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from verilex_runtime.tools.registry import ToolError


def sandbox_available() -> bool:
    """Return whether a usable Seatbelt backend exists on this host."""
    return shutil.which("sandbox-exec") is not None


def build_sandbox_profile(root: Path, *, protected_paths: tuple[Path, ...] = (), allow_write: bool = True) -> str:
    """Build a Seatbelt SBPL string confining writes to ``root`` and denying network.

    The profile is closed by default. It allows the process to boot (exec, fork,
    sysctl, system read-only paths, scratch temp dirs), lets it write only
    inside ``root`` (and the OS tmp scratch dirs), and denies outbound network.
    """
    root_str = str(root.resolve())
    # The workspace root goes in verbatim (argv, not a shell string), so it is
    # safe to embed even with spaces; escape quotes defensively.
    root_token = root_str.replace(chr(92), chr(92) * 2).replace('"', '\\"')
    profile = (
        "(version 1)\n"
        "(deny default)\n"
        "\n"
        "; Bootstrap the runtime the loader needs.\n"
        "(allow process-exec)\n"
        "(allow process-fork)\n"
        "(allow signal (target same-sandbox))\n"
        "(allow process-info* (target same-sandbox))\n"
        "(allow sysctl-read)\n"
        "\n"
        "; Reads must stay broad: a narrower file-read* whitelist crashes the\n"
        "; dyld loader (SIGABRT) on modern macOS because the loader resolves a\n"
        "; dynamic set of framework paths. Read isolation is instead enforced\n"
        "; at the application layer by the file tools' path traversal checks.\n"
        "(allow file-read*)\n"
        "(allow file-read-metadata)\n"
        "\n"
        "; Scratch space so tools can create temp files.\n"
        "(allow file-read* file-write* (subpath \"/tmp\"))\n"
        "(allow file-read* file-write* (subpath \"/private/tmp\"))\n"
        "(allow file-read* file-write* (subpath \"/var/tmp\"))\n"
        "(allow file-read* file-write* (subpath \"/private/var/tmp\"))\n"
        "\n"
        "; Workspace writes are authorized; everything else is denied below.\n"
        f"(allow file-write* (subpath \"{root_token}\"))\n"
        "(deny file-write*)\n"
        "\n"
        "; No network egress (DNS, connect, bind).\n"
        "(deny network*)\n"
    )
    if not allow_write:
        profile = profile.replace(f'(allow file-write* (subpath "{root_token}"))\n', '')
        profile += f'(deny file-write* (subpath "{root_token}"))\n'
    for protected in protected_paths:
        path = protected.resolve()
        token = str(path).replace('\\', '\\\\').replace('"', '\\"')
        profile += f'(deny file-write* file-link (subpath "{token}"))\n'
        for parent in path.parents:
            if parent != root.resolve() and root.resolve() not in parent.parents:
                break
            token = str(parent).replace('\\', '\\\\').replace('"', '\\"')
            profile += f'(deny file-write-unlink (literal "{token}"))\n'
    return profile


def build_sandbox_command(argv: list[str], root: Path, *, protected_paths: tuple[Path, ...] = (), allow_write: bool = True) -> list[str]:
    """Return ``argv`` prefixed with the ``sandbox-exec -p`` wrapper.

    If no backend is available, refuse contexts requiring write isolation.
    Ordinary fully writable contexts retain the existing platform behavior.
    """
    if not sandbox_available():
        if protected_paths or not allow_write:
            raise ToolError("sandbox_unavailable", "本机无法隔离命令对只读材料的写入，请使用受控文件工具。")
        return argv
    # Seatbelt matches paths, not inode identity. An existing hard link could
    # otherwise write the same input through an unprotected path. File tools
    # can check samefile directly; a subprocess cannot establish that boundary.
    if any(path.is_file() and path.stat().st_nlink > 1 for path in protected_paths):
        raise ToolError("protected_source", "当前只读材料存在硬链接，命令沙箱无法保证原件隔离，请使用受控文件工具。")
    return ["/usr/bin/sandbox-exec", "-p", build_sandbox_profile(root, protected_paths=protected_paths, allow_write=allow_write), *argv]
