from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Iterable


_COMPILED_MANIFEST_SHA256: str | None = None
_SYSTEM_READ_ALLOWLIST = (
    "/System/Library",
    "/System/Volumes/Preboot/Cryptexes",
    "/usr/lib",
    "/usr/bin",
    "/usr/share",
    "/bin",
    "/dev",
    "/private/etc",
    "/private/var/db/timezone",
    "/Library/Fonts",
)


class BundleVerificationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedComponentBundle:
    manifest_sha256: str
    component_ids: tuple[str, ...]
    system_read_allowlist: tuple[str, ...]


_MANIFEST_KEYS = {
    "schema_version",
    "selected_engine",
    "system_read_allowlist",
    "components",
}
_COMPONENT_KEYS = {
    "id",
    "version",
    "platform",
    "arch",
    "relative_path",
    "size_bytes",
    "sha256",
    "license_paths",
    "minimum_macos",
    "role",
}


def canonical_manifest_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def compiled_document_manifest_sha256() -> str | None:
    """Return only the value embedded by the packaged Runtime build hook."""
    value = _COMPILED_MANIFEST_SHA256
    if (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        return value
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_file(bundle_dir: Path, value: object, code: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BundleVerificationError(code)
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise BundleVerificationError(code)
    path = bundle_dir.joinpath(*relative.parts)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BundleVerificationError(code) from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise BundleVerificationError(code)
    try:
        path.resolve(strict=True).relative_to(bundle_dir.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise BundleVerificationError(code) from exc
    return path, relative.as_posix()


def _version(value: object, code: str) -> tuple[int, ...]:
    if not isinstance(value, str):
        raise BundleVerificationError(code)
    try:
        parts = tuple(int(part) for part in value.split("."))
    except ValueError as exc:
        raise BundleVerificationError(code) from exc
    if not parts or any(part < 0 for part in parts):
        raise BundleVerificationError(code)
    return parts


def _expect_sha256(value: object, code: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BundleVerificationError(code)
    return value


def verify_component_bundle(
    bundle_dir: Path,
    *,
    compiled_manifest_sha256: str | None,
    expected_platform: str,
    expected_arch: str,
    current_macos: str,
) -> VerifiedComponentBundle:
    if compiled_manifest_sha256 is None:
        raise BundleVerificationError("manifest_trust_root_unavailable")
    expected_manifest_hash = _expect_sha256(
        compiled_manifest_sha256, "manifest_trust_root_invalid"
    )
    manifest_path = bundle_dir / "component-manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise BundleVerificationError("manifest_missing") from exc
    actual_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_manifest_hash != expected_manifest_hash:
        raise BundleVerificationError("manifest_hash_mismatch")
    try:
        manifest: Any = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleVerificationError("manifest_invalid") from exc
    if canonical_manifest_bytes(manifest) != manifest_bytes:
        raise BundleVerificationError("manifest_not_canonical")
    if (
        not isinstance(manifest, dict)
        or set(manifest) != _MANIFEST_KEYS
        or manifest.get("schema_version") != 1
        or manifest.get("selected_engine") not in {"docxodus", "adeu_stack"}
        or not isinstance(manifest.get("components"), list)
        or not manifest["components"]
        or not isinstance(manifest.get("system_read_allowlist"), list)
    ):
        raise BundleVerificationError("manifest_invalid")

    current_version = _version(current_macos, "current_macos_invalid")
    component_ids: list[str] = []
    listed_files = {"component-manifest.json"}
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    referenced_license_paths: set[str] = set()
    for raw in manifest["components"]:
        if not isinstance(raw, dict) or set(raw) != _COMPONENT_KEYS:
            raise BundleVerificationError("component_record_invalid")
        component_id = raw["id"]
        if (
            not isinstance(component_id, str)
            or not component_id
            or component_id in seen_ids
        ):
            raise BundleVerificationError("component_id_invalid")
        if not isinstance(raw["version"], str) or not raw["version"].strip():
            raise BundleVerificationError("component_version_invalid")
        if not isinstance(raw["role"], str) or not raw["role"].strip():
            raise BundleVerificationError("component_role_invalid")
        if raw["platform"] != expected_platform or raw["arch"] != expected_arch:
            raise BundleVerificationError("component_platform_mismatch")
        minimum_macos = _version(
            raw["minimum_macos"], "component_minimum_macos_invalid"
        )
        if minimum_macos > current_version:
            raise BundleVerificationError("component_minimum_macos_mismatch")
        component_path, relative_path = _relative_file(
            bundle_dir, raw["relative_path"], "component_path_invalid"
        )
        if relative_path in seen_paths:
            raise BundleVerificationError("component_path_invalid")
        if (
            not isinstance(raw["size_bytes"], int)
            or isinstance(raw["size_bytes"], bool)
            or raw["size_bytes"] < 0
            or component_path.stat().st_size != raw["size_bytes"]
        ):
            raise BundleVerificationError("component_size_mismatch")
        if _sha256(component_path) != _expect_sha256(
            raw["sha256"], "component_hash_invalid"
        ):
            raise BundleVerificationError("component_hash_mismatch")
        license_paths = raw["license_paths"]
        if not isinstance(license_paths, list) or not license_paths:
            raise BundleVerificationError("component_license_missing")
        for license_value in license_paths:
            _, license_relative = _relative_file(
                bundle_dir, license_value, "component_license_missing"
            )
            listed_files.add(license_relative)
            referenced_license_paths.add(license_relative)
        seen_ids.add(component_id)
        seen_paths.add(relative_path)
        listed_files.add(relative_path)
        component_ids.append(component_id)

    if not referenced_license_paths <= seen_paths:
        raise BundleVerificationError("component_license_unhashed")

    allowlist = manifest["system_read_allowlist"]
    if tuple(allowlist) != _SYSTEM_READ_ALLOWLIST:
        raise BundleVerificationError("system_read_allowlist_invalid")
    actual_files: set[str] = set()
    for path in bundle_dir.rglob("*"):
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise BundleVerificationError("component_inventory_mismatch") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise BundleVerificationError("component_inventory_mismatch")
        if stat.S_ISREG(metadata.st_mode):
            actual_files.add(path.relative_to(bundle_dir).as_posix())
    if actual_files != listed_files:
        raise BundleVerificationError("component_inventory_mismatch")
    return VerifiedComponentBundle(
        manifest_sha256=actual_manifest_hash,
        component_ids=tuple(component_ids),
        system_read_allowlist=tuple(allowlist),
    )


def _seatbelt_quote(path: Path | str) -> str:
    value = str(path)
    if "\x00" in value or "\n" in value or "\r" in value:
        raise BundleVerificationError("seatbelt_path_invalid")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_seatbelt_profile(
    template_path: Path,
    *,
    task_dir: Path,
    resource_dir: Path,
    system_read_allowlist: Iterable[str],
) -> str:
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BundleVerificationError("seatbelt_template_missing") from exc
    required = {
        "{{TASK_DIR}}",
        "{{RESOURCE_DIR}}",
        "{{SYSTEM_READ_RULES}}",
        "{{PRIVATE_READ_DENY_RULES}}",
    }
    if not all(token in template for token in required):
        raise BundleVerificationError("seatbelt_template_invalid")
    system_rules = "\n".join(
        f'(allow file-read* (subpath "{_seatbelt_quote(path)}"))'
        for path in system_read_allowlist
    )
    allowed_read_filters = [
        '(literal "/")',
        f'(subpath "{_seatbelt_quote(task_dir.resolve())}")',
        f'(subpath "{_seatbelt_quote(resource_dir.resolve())}")',
        *(f'(subpath "{_seatbelt_quote(path)}")' for path in system_read_allowlist),
    ]
    private_read_denies = "\n".join(
        [
            "(deny file-read-data\n"
            "  (require-not\n"
            "    (require-any\n"
            + "\n".join(f"      {rule}" for rule in allowed_read_filters)
            + ")))",
        ]
    )
    rendered = (
        template.replace("{{TASK_DIR}}", _seatbelt_quote(task_dir.resolve()))
        .replace("{{RESOURCE_DIR}}", _seatbelt_quote(resource_dir.resolve()))
        .replace("{{SYSTEM_READ_RULES}}", system_rules)
        .replace("{{PRIVATE_READ_DENY_RULES}}", private_read_denies)
    )
    if "{{" in rendered or "}}" in rendered:
        raise BundleVerificationError("seatbelt_template_invalid")
    return rendered
