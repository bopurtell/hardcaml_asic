"""Curated evidence archive for one finished TT/LibreLane run directory.

A run directory is ~235 MB, most of it intermediate stage state and redundant
layout formats (odb, mag, mag_gds) that nothing reads again. This copies the
immutable records out, tars the reports an acceptance decision is read from
together with the two final artifacts a resubmission or a re-check needs, and
preserves the complete verified immutable input bundle in input-bundle.tar.gz.

What is kept is an explicit list, not a size rule. What belongs in evidence is
decided by what a reader has to check; a size threshold would silently drop a new
report the first time a flow stage grew one, and silently admit a big one.

Deliberately NOT kept: the compiled gate-level simulator (checks/*/gate.out, 9 MB
of iverilog output) and every intermediate stage directory. Both are rebuildable
from what is kept plus the pinned toolchain, and neither is read by a check.

Careful: this does not write the README.md that sits beside the records in
a human-curated archive directory. That note is the human account of what the run showed, and a
generated stand-in would read as though someone had checked the result.

Installed as `hardcaml-asic-flow archive`; `scripts/archive.py` is the checkout's
adapter onto this same module.

Usage:
  hardcaml-asic-flow archive RUN_DIR --output archives/observable-physical
  hardcaml-asic-flow archive RUN_DIR --output DIR --force  # replace a populated DIR
  hardcaml-asic-flow archive RUN_DIR --list                # the selection, writes nothing
"""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile

from . import coarse_flow_oracle

# Tar members, relative to the run directory. "{flow}" is run.json's outputs.flow
# rather than a literal "project/runs/asic", so a run whose flow directory moved
# still archives. A directory brings its whole subtree.
#
# The two synthesis and STA stage directories are taken whole (264 KB and 1.1 MB)
# instead of cherry-picked: at that size the risk of missing a report someone
# later needs costs more than the bytes.
KEEP = (
    "execution.log",
    "project/src/config.json",
    "{flow}/final/metrics.csv",
    "{flow}/final/metrics.json",
    "{flow}/final/sdc",
    "{flow}/final/gds",
    "{flow}/final/nl",
    "{flow}/06-yosys-synthesis",
    "{flow}/55-openroad-stapostpnr",
)

# Per postcheck attempt, relative to checks/<id>. gate.out is excluded here; see
# the module docstring.
KEEP_PER_CHECK = ("postcheck.log", "testbench.v", "tt/precheck/reports")

# Records copied beside the archive, uncompressed, so a reader can grep them
# without unpacking anything. manifest.json and postcheck.json are found rather
# than named: they live inside the run tree.
RECORDS = ("run.json", "results.json", "preflight.json")
GENERATED = {
    "archive.json", "input-bundle.sha256", "input-bundle.tar.gz",
    "manifest.json", "preflight.json", "reports.tar.gz", "results.json",
    "run.json",
}


def members(run_dir, flow):
    """Every path to archive, as (absolute path, name inside the tar).

    Missing entries are returned as omissions rather than raising: a synthesis
    only run has no final/gds and no checks, and that is a legitimate thing to
    archive. The caller decides which absences matter.
    """
    selected = []
    omitted = []
    candidates = [run_dir / entry.format(flow=flow) for entry in KEEP]
    for check in sorted((run_dir / "checks").glob("*/")):
        candidates += [check / entry for entry in KEEP_PER_CHECK]
    for path in candidates:
        name = str(path.relative_to(run_dir))
        parent = run_dir
        for part in path.relative_to(run_dir).parts:
            parent = parent / part
            if parent.is_symlink():
                raise coarse_flow_oracle.PrerequisiteError(
                    f"archive selection contains a symlink: {name}")
        if not path.exists():
            omitted.append(name)
        elif path.is_dir():
            # Sorted so the tar's member order does not depend on readdir order,
            # which is what makes two archives of one run compare equal.
            found = []
            for child in sorted(path.rglob("*")):
                if child.is_symlink():
                    raise coarse_flow_oracle.PrerequisiteError(
                        f"archive selection contains a symlink: {child.relative_to(run_dir)}")
                if child.is_file():
                    found.append((child, str(child.relative_to(run_dir))))
            # An empty directory counts as absent, not as a satisfied entry. A
            # stage that ran but wrote nothing leaves one behind, and reporting it
            # as present would hide exactly the case worth noticing.
            selected += found
            if not found:
                omitted.append(name)
        else:
            selected.append((path, name))
    return selected, omitted


def safe_relative(relative, label="bundle path"):
    if not isinstance(relative, str) or not relative:
        raise coarse_flow_oracle.PrerequisiteError(f"invalid {label}: {relative!r}")
    parts = relative.split("/")
    if (relative.startswith("/") or "\\" in relative
            or any(part in ("", ".", "..") for part in parts)):
        raise coarse_flow_oracle.PrerequisiteError(f"unsafe {label}: {relative!r}")
    return relative


def manifest_paths(manifest):
    """Validate the manifest inventory and return sorted (path, hash) pairs."""
    inventory = {}
    for entry in manifest["files"]:
        relative = safe_relative(entry["path"])
        expected = entry["sha256"]
        if relative == "manifest.json":
            raise coarse_flow_oracle.PrerequisiteError("manifest.json must not list itself")
        if relative in inventory:
            raise coarse_flow_oracle.PrerequisiteError(f"duplicate bundle path: {relative}")
        if (not isinstance(expected, str) or len(expected) != 64
                or any(character not in "0123456789abcdef" for character in expected)):
            raise coarse_flow_oracle.PrerequisiteError(f"invalid SHA-256 for bundle path: {relative}")
        inventory[relative] = expected

    for relative in inventory:
        parts = relative.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in inventory:
                raise coarse_flow_oracle.PrerequisiteError(
                    f"conflicting bundle paths: {parent} and {relative}")

    for field in ("synthesis_sources", "simulation_sources"):
        sources = manifest[field]
        if len(sources) != len(set(sources)):
            raise coarse_flow_oracle.PrerequisiteError(f"duplicate path in {field}")
        for relative in sources:
            if relative not in inventory:
                raise coarse_flow_oracle.PrerequisiteError(
                    f"{field} path is absent from manifest files: {relative}")

    copies = set()
    for source in manifest["source_inputs"]:
        source_path = safe_relative(source["path"], "source input path")
        relative = safe_relative(source["copy"], "source input copy")
        if relative != f"inputs/{source_path}":
            raise coarse_flow_oracle.PrerequisiteError(
                f"source input copy does not match its source path: {relative}")
        if relative in copies:
            raise coarse_flow_oracle.PrerequisiteError(f"duplicate source input copy: {relative}")
        copies.add(relative)
        if relative not in inventory or inventory[relative] != source["sha256"]:
            raise coarse_flow_oracle.PrerequisiteError(
                f"source input metadata disagrees with manifest files: {relative}")
    inventory_copies = {relative for relative in inventory if relative.startswith("inputs/")}
    if copies != inventory_copies:
        raise coarse_flow_oracle.PrerequisiteError(
            "source input metadata does not enumerate every inputs/ file")
    return sorted(inventory.items())


def regular_bundle_file(root, relative):
    """Resolve one inventory path while rejecting all symlink traversal."""
    path = root
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink():
            raise coarse_flow_oracle.PrerequisiteError(f"bundle path is a symlink: {relative}")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        raise coarse_flow_oracle.PrerequisiteError(f"missing bundle file: {relative}") from None
    if not resolved.is_relative_to(root.resolve()) or not path.is_file():
        raise coarse_flow_oracle.PrerequisiteError(f"invalid bundle file: {relative}")
    return path


def validate_bundle(root, record):
    if root.is_symlink():
        raise coarse_flow_oracle.PrerequisiteError(f"bundle root is a symlink: {root}")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise coarse_flow_oracle.PrerequisiteError(f"missing regular bundle manifest: {manifest_path}")
    manifest_sha256 = coarse_flow_oracle.digest(manifest_path)
    if manifest_sha256 != record["manifest_sha256"]:
        raise coarse_flow_oracle.PrerequisiteError(
            f"bundle manifest does not match the run record: {manifest_path}")
    manifest = coarse_flow_oracle.read_json(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("adapter") != "LibreLane":
        raise coarse_flow_oracle.PrerequisiteError(
            f"unsupported bundle schema or adapter: {manifest_path}")
    if manifest["identity"] != record["build_identity"]:
        raise coarse_flow_oracle.PrerequisiteError(
            f"bundle identity does not match the run record: {manifest_path}")
    inventory = manifest_paths(manifest)
    files = []
    for relative, expected in inventory:
        path = regular_bundle_file(root, relative)
        if coarse_flow_oracle.digest(path) != expected:
            raise coarse_flow_oracle.PrerequisiteError(f"changed bundle file: {relative}")
        files.append((path, relative))
    return manifest, files


def bundle_source(run_dir, record):
    """Select a complete original bundle, or a complete staged copy."""
    candidates = []
    original = Path(record["bundle"]).expanduser()
    if original.is_dir():
        candidates.append(("original", original.resolve()))
    staged = run_dir / record["outputs"]["project"]
    candidates.append(("staged", staged))

    errors = []
    for kind, root in candidates:
        try:
            manifest, files = validate_bundle(root, record)
            return kind, root, manifest, files
        except (OSError, ValueError, KeyError, coarse_flow_oracle.PrerequisiteError) as exc:
            errors.append(f"{kind} bundle {root}: {exc}")
    raise coarse_flow_oracle.PrerequisiteError(
        "no complete valid immutable bundle is available; " + "; ".join(errors))


def verify(run_dir, record, checks):
    """Re-check every hash the run's own records already pin.

    The point of the archive is to be the thing someone trusts later, so it is
    built from bytes that still match what the run recorded consuming. Returns
    the verified digests; raises on the first disagreement.
    """
    verified = {}
    staged = run_dir / record["outputs"]["project"] / "manifest.json"
    digest = coarse_flow_oracle.digest(staged)
    if digest != record["manifest_sha256"]:
        raise coarse_flow_oracle.PrerequisiteError(
            "staged manifest does not match the run record; archive the run that "
            f"produced it, not a modified copy ({staged})")
    verified["manifest_sha256"] = digest

    staged_manifest = coarse_flow_oracle.read_json(staged)
    if staged_manifest["identity"] != record["build_identity"]:
        raise coarse_flow_oracle.PrerequisiteError(
            "staged manifest identity does not match the run record")

    # The GDS, netlist and testbench are hashed into postcheck.json when the
    # checks run. Verifying them here is what lets the archive stand in for the
    # run directory: the bytes it carries are provably the bytes that passed.
    flow = run_dir / record["outputs"]["flow"]
    top = staged_manifest["top_module"]
    for check_dir, check in checks:
        inputs = check.get("inputs", {})
        for field, path in (
                ("gds_sha256", flow / "final/gds" / f"{top}.gds"),
                ("netlist_sha256", flow / "final/nl" / f"{top}.nl.v"),
                ("testbench_sha256", check_dir / "testbench.v")):
            expected = inputs.get(field)
            if expected is None or not path.is_file():
                continue
            if coarse_flow_oracle.digest(path) != expected:
                raise coarse_flow_oracle.PrerequisiteError(
                    f"{path.relative_to(run_dir)} does not match {field} in "
                    f"{(check_dir / 'postcheck.json').relative_to(run_dir)}; the run "
                    "directory was modified after the checks ran")
            verified[field] = expected
    return verified


def add_deterministic_file(stream, path, name):
    info = tarfile.TarInfo(name)
    info.size = path.stat().st_size
    info.mode = 0o644
    info.mtime = 0
    with path.open("rb") as source:
        stream.addfile(info, source)


def write_input_bundle(path, manifest_path, files):
    """Write only verified manifest-declared bytes with stable metadata."""
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as stream:
                add_deterministic_file(stream, manifest_path, "bundle/manifest.json")
                for source, relative in files:
                    add_deterministic_file(stream, source, f"bundle/{relative}")


def verify_input_bundle(path, record, inventory):
    expected = {"bundle/manifest.json": record["manifest_sha256"]}
    expected.update({f"bundle/{relative}": digest for relative, digest in inventory})
    with tarfile.open(path, "r:gz") as stream:
        members = stream.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise coarse_flow_oracle.PrerequisiteError("input bundle archive has conflicting members")
        for member in members:
            if not member.isfile():
                raise coarse_flow_oracle.PrerequisiteError(
                    f"input bundle archive member is not a regular file: {member.name}")
            source = stream.extractfile(member)
            actual = hashlib.sha256(source.read()).hexdigest()
            if actual != expected[member.name]:
                raise coarse_flow_oracle.PrerequisiteError(
                    f"input bundle archive member changed while packaging: {member.name}")


def owned_file(path):
    return path.name in GENERATED or (
        path.name.startswith("postcheck-") and path.name.endswith(".json")) or (
        path.name == "postcheck.json")


def publish(staging, output):
    """Atomically replace archive-owned files while retaining user files."""
    if output.exists():
        for path in output.iterdir():
            if not owned_file(path):
                destination = staging / path.name
                if path.is_dir():
                    shutil.copytree(path, destination, symlinks=True)
                elif path.is_symlink():
                    destination.symlink_to(os.readlink(path))
                else:
                    shutil.copy2(path, destination)

    backup = Path(tempfile.mkdtemp(
        prefix=f".{output.name}.archive-backup-{os.getpid()}-", dir=output.parent))
    backup.rmdir()
    moved_old = False
    try:
        if output.exists():
            os.replace(output, backup)
            moved_old = True
        os.replace(staging, output)
    except Exception:
        if moved_old and not output.exists():
            os.replace(backup, output)
        raise
    if moved_old:
        try:
            shutil.rmtree(backup)
        except OSError:
            # Publication has already completed atomically. A stale hidden backup
            # is preferable to reporting failure for a valid new evidence tree.
            pass


def archive(run_dir, output, force):
    record = coarse_flow_oracle.read_json(run_dir / "run.json")
    if record["status"] != "completed":
        raise coarse_flow_oracle.PrerequisiteError(
            f"run status is {record['status']}, not completed; archive a finished run")
    flow = record["outputs"]["flow"]

    checks = [(path.parent, coarse_flow_oracle.read_json(path))
              for path in sorted((run_dir / "checks").glob("*/postcheck.json"))]
    verified = verify(run_dir, record, checks)
    source_kind, bundle_root, bundle_manifest, bundle_files = bundle_source(run_dir, record)
    selected, omitted = members(run_dir, flow)

    if output.exists() and any(output.iterdir()) and not force:
        raise coarse_flow_oracle.PrerequisiteError(
            f"{output} already has files; use --force to replace them")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.archive-", dir=output.parent))
    try:
        manifest_source = bundle_root / "manifest.json"
        shutil.copyfile(manifest_source, staging / "manifest.json")
        if coarse_flow_oracle.digest(staging / "manifest.json") != record["manifest_sha256"]:
            raise coarse_flow_oracle.PrerequisiteError("manifest changed while copying it")
        for name in RECORDS:
            # An absent record is reported, never skipped quietly: runs from before
            # flow.sh saved preflight.json have none, and an evidence directory that
            # is missing one should say so rather than look complete.
            if (run_dir / name).is_file():
                shutil.copy2(run_dir / name, staging / name)
            else:
                omitted.append(name)
        for check_dir, _ in checks:
            # One postcheck attempt keeps the plain name the README links; a rerun
            # gets its check id, so neither silently overwrites the other.
            name = ("postcheck.json" if len(checks) == 1
                    else f"postcheck-{check_dir.name}.json")
            shutil.copy2(check_dir / "postcheck.json", staging / name)

        reports = staging / "reports.tar.gz"
        with tarfile.open(reports, "w:gz") as stream:
            for path, name in selected:
                stream.add(path, arcname=name)

        input_bundle = staging / "input-bundle.tar.gz"
        write_input_bundle(input_bundle, manifest_source, bundle_files)
        verify_input_bundle(input_bundle, record, manifest_paths(bundle_manifest))
        input_digest = coarse_flow_oracle.digest(input_bundle)
        (staging / "input-bundle.sha256").write_text(
            f"{input_digest}  input-bundle.tar.gz\n")
        input_members = ["bundle/manifest.json"] + [
            f"bundle/{relative}" for _, relative in bundle_files]

        coarse_flow_oracle.save(staging / "archive.json", {
            "schema_version": 1,
            "run_id": record["run_id"],
            "build_identity": record["build_identity"],
            "created_at": coarse_flow_oracle.now(),
            "source_run": str(run_dir),
            "archive": reports.name,
            "archive_bytes": reports.stat().st_size,
            "input_bundle": {
                "archive": input_bundle.name,
                "archive_bytes": input_bundle.stat().st_size,
                "sha256": input_digest,
                "root": "bundle",
                "source": source_kind,
                "manifest_sha256": record["manifest_sha256"],
                "build_identity": bundle_manifest["identity"],
                "manifest_files_verified": len(bundle_files),
                "source_inputs_verified": len(bundle_manifest["source_inputs"]),
                "members": input_members,
            },
            "verified": verified,
            "members": sorted(name for _, name in selected),
            "omitted": sorted(omitted),
            "records": sorted(path.name for path in staging.glob("*.json")
                              if path.name != "archive.json"),
        })
        publish(staging, output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return output / "reports.tar.gz", selected, omitted


def list_archive(run_dir, output=print, diagnostics=None):
    diagnostics = diagnostics or (lambda *values: print(*values, file=sys.stderr))
    record = coarse_flow_oracle.read_json(run_dir / "run.json")
    if record["status"] != "completed":
        raise coarse_flow_oracle.PrerequisiteError(
            f"run status is {record['status']}, not completed; list a finished run")
    checks = [(path.parent, coarse_flow_oracle.read_json(path))
              for path in sorted((run_dir / "checks").glob("*/postcheck.json"))]
    verify(run_dir, record, checks)
    selected, omitted = members(run_dir, record["outputs"]["flow"])
    kind, _, _, bundle_files = bundle_source(run_dir, record)
    total = sum(path.stat().st_size for path, _ in selected)
    for _, name in selected:
        output(name)
    output("input-bundle: bundle/manifest.json")
    for _, name in bundle_files:
        output(f"input-bundle: bundle/{name}")
    for name in omitted:
        diagnostics(f"absent: {name}")
    diagnostics(f"\n{len(selected)} report files, {total / 1e6:.1f} MB uncompressed; "
                f"{len(bundle_files) + 1} complete input-bundle files from {kind}")
    return selected, omitted


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path, help="Evidence directory to write")
    parser.add_argument("--force", action="store_true",
                        help="Replace files in a populated --output")
    parser.add_argument("--list", action="store_true",
                        help="Print report and complete input-bundle selections; write nothing")
    args = parser.parse_args()
    try:
        run_dir = args.run_dir.resolve()
        if args.list:
            list_archive(run_dir)
            return 0
        if not args.output:
            raise coarse_flow_oracle.PrerequisiteError("--output is required unless --list is given")
        tarball, selected, omitted = archive(run_dir, args.output.resolve(), args.force)
        for name in omitted:
            print(f"archive: absent, not included: {name}", file=sys.stderr)
        print(f"archive: {len(selected)} files -> {tarball} "
              f"({tarball.stat().st_size / 1e6:.2f} MB)")
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            coarse_flow_oracle.PrerequisiteError) as exc:
        print(f"archive: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
