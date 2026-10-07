#!/usr/bin/env python3
"""Converge explicitly-owned first-party products to validated base heads.

The inventory is a positive authority list. Repository discovery, installed
commands, plugins, skills, MCP servers, and dependencies never grant authority.
`check` and `plan` are read-only; `apply` revalidates every source fingerprint
before using a product's own installer and lifecycle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import horonom_workspace

REPO_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PATH = REPO_ROOT / "governance" / "workspace" / "first-party-products.yaml"
LOCAL_STATE_PARTS = (".local", "state")
CARGO_BIN_PARTS = (".cargo", "bin")
RECEIPT_SCHEMA = 1
STATUSES = frozenset({"CURRENT", "DRIFTED", "UNVERIFIABLE", "BASE_HEAD_NOT_DEPLOYABLE"})
DISPOSITIONS = frozenset({"managed", "unverifiable", "not_present"})
LIFECYCLES = frozenset({"libra", "circinus"})
SAFE_ID = re.compile(r"^[a-z][a-z0-9-]*$")
SAFE_REPO = re.compile(r"^[A-Za-z0-9._-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
GITHUB_REMOTE = re.compile(r"^(?:https://github\.com/|git@github\.com:)([^/]+)/([^/]+?)(?:\.git)?$")


class ConvergenceError(RuntimeError):
    """A fail-closed inventory, source, or lifecycle error."""


@dataclass(frozen=True)
class Product:
    id: str
    workspace_name: str
    checkout: str
    org: str
    repo: str
    disposition: str
    lifecycle: str | None
    validation: tuple[tuple[str, ...], ...]
    installer: tuple[tuple[str, ...], ...] | None
    reason: str | None


@dataclass(frozen=True)
class Exclusion:
    id: str
    owner: str
    repository: str
    policy_owner: str


@dataclass(frozen=True)
class Inventory:
    products: tuple[Product, ...]
    exclusions: tuple[Exclusion, ...]
    digest: str


@dataclass
class SourceState:
    checkout: Path | None = None
    remote: str | None = None
    base_branch: str | None = None
    remote_head: str | None = None
    local_head: str | None = None
    clean: bool | None = None
    main_worktree: bool | None = None
    error: str | None = None


class Runner:
    """Small injectable argv runner; never invokes a shell."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: int = 120,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        if check and result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()
            summary = detail[-1] if detail else f"exit {result.returncode}"
            raise ConvergenceError(f"{argv[0]} failed: {summary}")
        return result


def _command(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise ConvergenceError(f"{where}: command must be a non-empty argv array")
    for item in value:
        if "\x00" in item or "\n" in item or "\r" in item:
            raise ConvergenceError(f"{where}: unsafe command argument")
    return tuple(value)


def _inventory_document(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConvergenceError(f"could not read inventory {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConvergenceError("first-party inventory: root must be an object")
    if data.get("schema_version") != 1:
        raise ConvergenceError("first-party inventory: unsupported schema_version")
    return raw, data


def _managed_lifecycle(item: dict[str, Any], where: str) -> tuple[
    str | None, tuple[tuple[str, ...], ...], tuple[tuple[str, ...], ...] | None
]:
    lifecycle = item.get("lifecycle")
    validation_raw = item.get("validation", [])
    installer_raw = item.get("installer")
    if item["disposition"] != "managed":
        if any(key in item for key in ("lifecycle", "validation", "installer")):
            raise ConvergenceError(f"{where}: non-managed product may not carry executable lifecycle fields")
        return None, (), None
    if lifecycle not in LIFECYCLES:
        raise ConvergenceError(f"{where}: managed product needs a known lifecycle")
    if not isinstance(validation_raw, list) or not validation_raw:
        raise ConvergenceError(f"{where}: managed product needs validation commands")
    if not isinstance(installer_raw, list) or not installer_raw:
        raise ConvergenceError(f"{where}.installer: expected one or more argv arrays")
    validation = tuple(_command(command, f"{where}.validation") for command in validation_raw)
    installer = tuple(_command(command, f"{where}.installer") for command in installer_raw)
    return lifecycle, validation, installer


def _parse_product(
    item: Any,
    index: int,
    workspace: dict[str, dict[str, str]],
    seen: set[str],
) -> Product:
    where = f"products[{index}]"
    if not isinstance(item, dict):
        raise ConvergenceError(f"{where}: expected an object")
    required = ("id", "workspace_name", "checkout", "org", "repo", "disposition")
    if any(not isinstance(item.get(key), str) or not item[key] for key in required):
        raise ConvergenceError(f"{where}: missing required string field")
    product_id = item["id"]
    if not SAFE_ID.fullmatch(product_id) or product_id in seen:
        raise ConvergenceError(f"{where}: unsafe or duplicate id {product_id!r}")
    if not SAFE_ID.fullmatch(item["workspace_name"]):
        raise ConvergenceError(f"{where}: unsafe workspace_name")
    if not SAFE_REPO.fullmatch(item["checkout"]) or item["checkout"] in {".", ".."}:
        raise ConvergenceError(f"{where}: unsafe checkout")
    if item["org"] != "horonomy" or not SAFE_REPO.fullmatch(item["repo"]):
        raise ConvergenceError(f"{where}: only explicit horonomy repositories are first-party")
    if item["disposition"] not in DISPOSITIONS:
        raise ConvergenceError(f"{where}: invalid disposition")
    manifest = workspace.get(item["workspace_name"])
    expected = (item["org"], item["repo"], "product")
    observed = (manifest["org"], manifest["repo"], manifest["category"]) if manifest else None
    if observed != expected:
        raise ConvergenceError(f"{where}: source identity disagrees with workspace manifest")
    lifecycle, validation, installer = _managed_lifecycle(item, where)
    seen.add(product_id)
    return Product(
        id=product_id,
        workspace_name=item["workspace_name"],
        checkout=item["checkout"],
        org=item["org"],
        repo=item["repo"],
        disposition=item["disposition"],
        lifecycle=lifecycle,
        validation=validation,
        installer=installer,
        reason=item.get("reason"),
    )


def _parse_exclusion(item: Any, index: int, product_ids: set[str]) -> Exclusion:
    where = f"third_party_exclusions[{index}]"
    fields = {"id", "owner", "repository", "policy_owner"}
    if not isinstance(item, dict) or set(item) != fields:
        raise ConvergenceError(f"{where}: exclusion rows are metadata-only")
    if not all(isinstance(item[key], str) and item[key] for key in fields):
        raise ConvergenceError(f"{where}: invalid exclusion metadata")
    if item["id"] in product_ids:
        raise ConvergenceError(f"{where}: first-party and third-party IDs overlap")
    return Exclusion(**item)


def load_inventory(path: Path = INVENTORY_PATH) -> Inventory:
    raw, data = _inventory_document(path)
    products_raw = data.get("products")
    exclusions_raw = data.get("third_party_exclusions")
    if not isinstance(products_raw, list) or not isinstance(exclusions_raw, list):
        raise ConvergenceError("first-party inventory: products and exclusions must be arrays")
    workspace = {entry["name"]: entry for entry in horonom_workspace.load_manifest()}
    seen: set[str] = set()
    products = tuple(_parse_product(item, index, workspace, seen) for index, item in enumerate(products_raw))
    exclusions = tuple(_parse_exclusion(item, index, seen) for index, item in enumerate(exclusions_raw))
    return Inventory(products, exclusions, hashlib.sha256(raw).hexdigest())


def _checkout_path(root: Path, product: Product) -> Path | None:
    root = root.expanduser().resolve()
    for relative in (Path(product.checkout), Path("products") / product.workspace_name):
        unresolved = root / relative
        if unresolved.is_symlink():
            continue
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        git_dir = candidate / ".git"
        if candidate.is_dir() and git_dir.is_dir() and not git_dir.is_symlink():
            return candidate
    return None


def _remote_identity(url: str) -> tuple[str, str] | None:
    match = GITHUB_REMOTE.fullmatch(url.strip())
    return (match.group(1).lower(), match.group(2)) if match else None


def _canonical_remote(
    product: Product,
    checkout: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> str | None:
    remotes = runner(["git", "remote"], cwd=checkout)
    if remotes.returncode:
        return None
    matches = []
    for remote in remotes.stdout.splitlines():
        result = runner(["git", "remote", "get-url", remote], cwd=checkout)
        if result.returncode == 0 and _remote_identity(result.stdout) == (product.org, product.repo):
            matches.append(remote)
    return matches[0] if len(matches) == 1 else None


def _remote_base(
    remote: str,
    checkout: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[str, str] | None:
    symref = runner(["git", "ls-remote", "--symref", remote, "HEAD"], cwd=checkout)
    first = symref.stdout.splitlines()[0] if symref.returncode == 0 and symref.stdout.splitlines() else ""
    match = re.fullmatch(r"ref: refs/heads/([^\s]+)\s+HEAD", first)
    if not match:
        return None
    branch = match.group(1)
    result = runner(["git", "ls-remote", remote, f"refs/heads/{branch}"], cwd=checkout)
    head = result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else ""
    return (branch, head) if SHA.fullmatch(head) else None


def inspect_source(product: Product, root: Path, runner: Callable[..., subprocess.CompletedProcess[str]]) -> SourceState:
    state = SourceState(checkout=_checkout_path(root, product))
    if state.checkout is None:
        state.error = "checkout_missing_or_unsafe"
        return state
    checkout = state.checkout
    state.main_worktree = (checkout / ".git").is_dir() and not (checkout / ".git").is_symlink()
    if not state.main_worktree:
        state.error = "linked_worktree"
        return state
    status = runner(["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout)
    if status.returncode or status.stdout.strip():
        state.clean = status.returncode == 0 and not bool(status.stdout.strip())
        state.error = "git_status_failed" if status.returncode else "tracked_checkout_dirty"
        return state
    state.clean = True
    state.remote = _canonical_remote(product, checkout, runner)
    if state.remote is None:
        state.error = "canonical_remote_not_unique"
        return state
    base = _remote_base(state.remote, checkout, runner)
    if base is None:
        state.error = "remote_default_unavailable"
        return state
    state.base_branch, state.remote_head = base
    local = runner(["git", "rev-parse", "HEAD"], cwd=checkout)
    state.local_head = local.stdout.strip()
    if local.returncode or not SHA.fullmatch(state.local_head):
        state.error = "local_head_unresolved"
        return state
    branch = runner(["git", "branch", "--show-current"], cwd=checkout)
    if branch.returncode or branch.stdout.strip() != state.base_branch:
        state.error = "not_on_configured_base_branch"
    return state


def receipt_root() -> Path:
    base = os.environ.get("HORONOM_CONVERGENCE_STATE_DIR")
    return Path(base).expanduser() if base else Path.home().joinpath(*LOCAL_STATE_PARTS, "horonom", "convergence")


def receipt_path(product_id: str) -> Path:
    return receipt_root() / f"{product_id}.json"


def _read_receipt(product_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads(receipt_path(product_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (
        data
        if isinstance(data, dict)
        and data.get("schema_version") == RECEIPT_SCHEMA
        and data.get("product") == product_id
        else None
    )


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _process_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError):
        return False
    return True


def _libra_runtime(
    receipt: dict[str, Any] | None,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    binary = Path.home().joinpath(*CARGO_BIN_PARTS, "libra-governor")
    artifact_hash = _sha256(binary)
    installed = None
    running = None
    healthy = False
    if receipt and receipt.get("artifact_sha256") == artifact_hash:
        installed = receipt.get("source_revision")
    pid_path = Path.home().joinpath(*LOCAL_STATE_PARTS, "libra-governor", "daemon.pid")
    try:
        pid_value = json.loads(pid_path.read_text(encoding="utf-8"))
        pid = pid_value if isinstance(pid_value, dict) else {}
    except (OSError, json.JSONDecodeError):
        pid = {}
    recorded_hash = pid.get("exe_sha256")
    doctor = runner([str(binary), "doctor", "--json"], timeout=30) if binary.exists() else None
    doctor_reachable = False
    if doctor and doctor.returncode == 0:
        try:
            payload = json.loads(doctor.stdout)
            doctor_reachable = any(
                item.get("severity") == "ok" and str(item.get("message", "")).startswith("daemon ")
                for item in payload.get("findings", [])
            )
        except json.JSONDecodeError:
            pass
    healthy = bool(
        _process_alive(pid.get("pid"))
        and recorded_hash
        and recorded_hash == artifact_hash
        and pid.get("exe_path") == str(binary)
        and doctor_reachable
    )
    if installed and healthy:
        running = installed
    return {
        "installed_revision": installed,
        "running_revision": running,
        "running_healthy": healthy,
        "artifact_sha256": artifact_hash,
    }


def _circinus_runtime(receipt: dict[str, Any] | None, runner: Callable[..., subprocess.CompletedProcess[str]]) -> dict[str, Any]:
    command = shutil.which("circinus")
    manifest_path = Path.home().joinpath(*LOCAL_STATE_PARTS, "circinus", "install.json")
    manifest_hash = None
    installed_hash = None
    try:
        manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = manifest_value if isinstance(manifest_value, dict) else {}
        manifest_hash = manifest.get("entrypoint_sha256")
    except (OSError, json.JSONDecodeError):
        pass
    if command:
        tool_root = Path(command).resolve().parent
        python = tool_root / "python"
        identity = runner(
            [
                str(python),
                "-c",
                "from circinus.daemon.identity import compute_entrypoint_identity; "
                "import json; print(json.dumps(compute_entrypoint_identity().to_json()))",
            ],
            timeout=30,
        )
        if identity.returncode == 0:
            try:
                identity_payload = json.loads(identity.stdout)
                if isinstance(identity_payload, dict):
                    installed_hash = identity_payload.get("package_tree_sha256")
            except json.JSONDecodeError:
                pass
    installed = None
    if (
        isinstance(manifest_hash, str)
        and SHA.fullmatch(manifest_hash)
        and manifest_hash == installed_hash
        and receipt
        and receipt.get("artifact_sha256") == installed_hash
    ):
        installed = receipt.get("source_revision")
    status = runner([command, "doctor", "--json"], timeout=30) if command else None
    healthy = False
    identity_matches = False
    if status and status.returncode == 0:
        try:
            payload = json.loads(status.stdout)
            raw_checks = payload.get("checks", []) if isinstance(payload, dict) else []
            checks = {
                item.get("id"): item
                for item in raw_checks
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            healthy = checks.get("daemon_reachable", {}).get("status") == "pass"
            identity_matches = checks.get("daemon_entrypoint_identity", {}).get("status") == "pass"
        except json.JSONDecodeError:
            pass
    running = installed if installed and healthy and identity_matches else None
    return {
        "installed_revision": installed,
        "running_revision": running,
        "running_healthy": healthy,
        "artifact_sha256": installed_hash,
    }


def inspect_runtime(product: Product, runner: Callable[..., subprocess.CompletedProcess[str]]) -> dict[str, Any]:
    receipt = _read_receipt(product.id)
    if product.lifecycle == "libra":
        return _libra_runtime(receipt, runner)
    if product.lifecycle == "circinus":
        return _circinus_runtime(receipt, runner)
    return {"installed_revision": None, "running_revision": None, "running_healthy": None}


def run_validation(product: Product, source: SourceState, runner: Callable[..., subprocess.CompletedProcess[str]]) -> dict[str, Any]:
    if source.error or source.local_head != source.remote_head or source.checkout is None:
        return {"status": "not_run", "commands": []}
    outcomes = []
    for command in product.validation:
        try:
            result = runner(command, cwd=source.checkout, timeout=600)
        except (subprocess.TimeoutExpired, OSError):
            outcomes.append({"argv": list(command), "exit_code": None, "reason": "unverifiable"})
            return {"status": "unverifiable", "commands": outcomes}
        outcomes.append({"argv": list(command), "exit_code": result.returncode})
        if result.returncode:
            return {"status": "failed", "commands": outcomes}
    return {"status": "passed", "commands": outcomes}


def derive_status(
    product: Product,
    source: SourceState,
    runtime: dict[str, Any],
    validation: dict[str, Any],
) -> str:
    if product.disposition != "managed" or source.error:
        return "UNVERIFIABLE"
    if validation["status"] == "failed":
        return "BASE_HEAD_NOT_DEPLOYABLE"
    if source.local_head != source.remote_head:
        return "DRIFTED"
    if validation["status"] != "passed":
        return "UNVERIFIABLE"
    installed = runtime.get("installed_revision")
    running = runtime.get("running_revision")
    if installed is None or running is None or runtime.get("running_healthy") is not True:
        return "UNVERIFIABLE"
    if installed != source.remote_head or running != source.remote_head:
        return "DRIFTED"
    return "CURRENT"


def inspect_product(
    product: Product,
    root: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    *,
    validate: bool,
) -> dict[str, Any]:
    source = inspect_source(product, root, runner)
    runtime = inspect_runtime(product, runner) if product.disposition == "managed" else {
        "installed_revision": None,
        "running_revision": None,
        "running_healthy": None,
    }
    validation = run_validation(product, source, runner) if validate and product.disposition == "managed" else {
        "status": "not_run",
        "commands": [],
    }
    status = derive_status(product, source, runtime, validation)
    return {
        "product": product.id,
        "repository": f"{product.org}/{product.repo}",
        "disposition": product.disposition,
        "base_branch": source.base_branch,
        "remote_head": source.remote_head,
        "local_head": source.local_head,
        "source_error": source.error,
        "installed_revision": runtime.get("installed_revision"),
        "running_revision": runtime.get("running_revision"),
        "running_healthy": runtime.get("running_healthy"),
        "validation": validation,
        "status": status,
        "reason": product.reason,
    }


def inspect_inventory(
    inventory: Inventory,
    root: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    *,
    product_ids: set[str] | None,
    validate: bool,
) -> dict[str, Any]:
    selected = [p for p in inventory.products if product_ids is None or p.id in product_ids]
    unknown = (product_ids or set()) - {p.id for p in inventory.products}
    if unknown:
        raise ConvergenceError(f"unknown or non-first-party product(s): {', '.join(sorted(unknown))}")
    first_party = [inspect_product(p, root, runner, validate=validate) for p in selected]
    third_party = [
        {
            "tool": item.id,
            "owner": item.owner,
            "repository": item.repository,
            "policy_owner": item.policy_owner,
            "status": "EXCLUDED",
        }
        for item in inventory.exclusions
    ]
    return {
        "schema_version": 1,
        "desired_revision_policy": "latest_validated_configured_remote_base_branch_head",
        "inventory_digest": inventory.digest,
        "first_party_products": first_party,
        "third_party_tools": third_party,
    }


def _write_receipt(product: Product, revision: str, artifact_sha256: str) -> None:
    path = receipt_path(product.id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "schema_version": RECEIPT_SCHEMA,
        "product": product.id,
        "source_revision": revision,
        "artifact_sha256": artifact_sha256,
        "installed_at": int(time.time()),
    }
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _artifact_path(product: Product) -> Path:
    if product.lifecycle == "libra":
        return Path.home().joinpath(*CARGO_BIN_PARTS, "libra-governor")
    command = shutil.which("circinus") if product.lifecycle == "circinus" else None
    if not command:
        raise ConvergenceError(f"{product.id}: installed artifact not found")
    return Path(command)


def _artifact_measurement(product: Product) -> str | None:
    if product.lifecycle == "libra":
        return _sha256(Path.home().joinpath(*CARGO_BIN_PARTS, "libra-governor"))
    if product.lifecycle == "circinus":
        try:
            manifest = json.loads(
                (Path.home() / ".local/state/circinus/install.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        value = manifest.get("entrypoint_sha256")
        return value if isinstance(value, str) and value else None
    return None


def _revalidate_source(product: Product, root: Path, expected: SourceState, runner: Callable[..., subprocess.CompletedProcess[str]]) -> SourceState:
    current = inspect_source(product, root, runner)
    stable = (
        current.error is None
        and current.remote == expected.remote
        and current.base_branch == expected.base_branch
        and current.remote_head == expected.remote_head
        and current.local_head == expected.local_head
    )
    if not stable:
        raise ConvergenceError(f"{product.id}: source changed after validation")
    return current


def _reconcile_daemon(
    product: Product,
    target: str,
    prior_runtime: dict[str, Any],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    previous = prior_runtime.get("running_revision")
    if previous is None or previous == target:
        return
    artifact = str(_artifact_path(product))
    if product.lifecycle == "libra":
        runner([artifact, "daemon", "stop"], timeout=30, check=True)
    elif product.lifecycle == "circinus":
        runner([artifact, "stop"], timeout=30, check=True)
        runner([artifact, "start"], timeout=30, check=True)


def apply_product(
    product: Product,
    root: Path,
    inventory_digest: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    if product.disposition != "managed":
        raise ConvergenceError(f"{product.id}: disposition is not managed")
    before = inspect_source(product, root, runner)
    if before.error:
        raise ConvergenceError(f"{product.id}: {before.error}")
    assert before.checkout and before.remote and before.base_branch and before.remote_head

    runner(["git", "fetch", before.remote], cwd=before.checkout, timeout=300, check=True)
    refreshed = inspect_source(product, root, runner)
    if refreshed.error or refreshed.remote_head != before.remote_head:
        raise ConvergenceError(f"{product.id}: source changed while planning")
    if refreshed.local_head != refreshed.remote_head:
        runner(
            ["git", "merge", "--ff-only", f"{refreshed.remote}/{refreshed.base_branch}"],
            cwd=refreshed.checkout,
            timeout=120,
            check=True,
        )
        refreshed = inspect_source(product, root, runner)
    if refreshed.error or refreshed.local_head != refreshed.remote_head:
        raise ConvergenceError(f"{product.id}: base checkout did not converge by fast-forward")
    if load_inventory().digest != inventory_digest:
        raise ConvergenceError("first-party inventory changed while applying")

    validation = run_validation(product, refreshed, runner)
    if validation["status"] != "passed":
        return inspect_product(product, root, runner, validate=True)
    # Validation may be long-running. Re-resolve the remote/default/base SHA and
    # checkout fingerprint before invoking an installer so a base advance or a
    # concurrent local edit can never turn a previously valid plan into a stale
    # deployment source.
    preinstall = _revalidate_source(product, root, refreshed, runner)
    prior_runtime = inspect_runtime(product, runner)
    assert product.installer and preinstall.checkout and preinstall.remote_head
    for command in product.installer:
        runner(command, cwd=preinstall.checkout, timeout=1800, check=True)
    artifact_hash = _artifact_measurement(product)
    if not artifact_hash:
        raise ConvergenceError(f"{product.id}: installed artifact could not be measured")
    _write_receipt(product, preinstall.remote_head, artifact_hash)

    _reconcile_daemon(product, preinstall.remote_head, prior_runtime, runner)
    return inspect_product(product, root, runner, validate=True)


def _print_report(report: dict[str, Any]) -> None:
    print("A. FIRST-PARTY PRODUCTS")
    print("product\trepository\tbase\tremote\tlocal\tinstalled\trunning\tstatus")
    for row in report["first_party_products"]:
        print(
            "\t".join(
                str(row.get(key) or "-")
                for key in (
                    "product",
                    "repository",
                    "base_branch",
                    "remote_head",
                    "local_head",
                    "installed_revision",
                    "running_revision",
                    "status",
                )
            )
        )
    print("\nB. THIRD-PARTY TOOLS (EXCLUDED; NO ACTIONS)")
    print("tool\towner\trepository\tstatus")
    for row in report["third_party_tools"]:
        print("\t".join(str(row[key]) for key in ("tool", "owner", "repository", "status")))


def _selected(args: argparse.Namespace) -> set[str] | None:
    return set(args.product) if args.product else None


def _cmd_check(args: argparse.Namespace) -> int:
    inventory = load_inventory()
    report = inspect_inventory(
        inventory, Path(args.root), Runner(), product_ids=_selected(args), validate=args.validate
    )
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        print(output)
    else:
        _print_report(report)
    return 0 if all(row["status"] == "CURRENT" for row in report["first_party_products"] if row["disposition"] == "managed") else 1


def _cmd_plan(args: argparse.Namespace) -> int:
    inventory = load_inventory()
    report = inspect_inventory(inventory, Path(args.root), Runner(), product_ids=_selected(args), validate=True)
    report["mode"] = "plan"
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        print(output)
    else:
        _print_report(report)
        print("\nRead-only plan: no source, installer, process, or receipt state was changed.")
    return 0 if all(row["status"] == "CURRENT" for row in report["first_party_products"] if row["disposition"] == "managed") else 1


def _cmd_apply(args: argparse.Namespace) -> int:
    inventory = load_inventory()
    selected = _selected(args)
    products = [p for p in inventory.products if selected is None or p.id in selected]
    unknown = (selected or set()) - {p.id for p in inventory.products}
    if unknown:
        raise ConvergenceError(f"unknown or non-first-party product(s): {', '.join(sorted(unknown))}")
    rows = []
    runner = Runner()
    for product in products:
        if product.disposition == "managed":
            rows.append(apply_product(product, Path(args.root), inventory.digest, runner))
        else:
            rows.append(inspect_product(product, Path(args.root), runner, validate=False))
    report = {
        "schema_version": 1,
        "desired_revision_policy": "latest_validated_configured_remote_base_branch_head",
        "inventory_digest": inventory.digest,
        "first_party_products": rows,
        "third_party_tools": [
            {
                "tool": item.id,
                "owner": item.owner,
                "repository": item.repository,
                "policy_owner": item.policy_owner,
                "status": "EXCLUDED",
            }
            for item in inventory.exclusions
        ],
        "mode": "apply",
    }
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        print(output)
    else:
        _print_report(report)
    return 0 if all(row["status"] == "CURRENT" for row in rows if row["disposition"] == "managed") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("check", _cmd_check), ("plan", _cmd_plan), ("apply", _cmd_apply)):
        command = sub.add_parser(name)
        command.add_argument("--root", required=True, help="Directory containing managed product checkouts.")
        command.add_argument("--product", action="append", help="Restrict to an explicit first-party ID.")
        command.add_argument("--json", action="store_true")
        if name == "check":
            command.add_argument("--validate", action="store_true", help="Run declared base-head validation.")
        command.set_defaults(func=handler)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConvergenceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
