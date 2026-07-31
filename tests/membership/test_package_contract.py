"""The package contract, checked with synthetic archives only.

No database is touched here: `apps.membership.package` deliberately holds no
Django import, and these tests exercise it directly. That is also what lets the
approved package be validated on a machine with no PostgreSQL.
"""

from __future__ import annotations

import zipfile

import pytest

from apps.membership.package import PackageContractError, PackageLimits, read_package

from .package_factory import (
    PATH_A,
    SNAP_A_DIRECT,
    SOURCE_A,
    build_package,
    default_monthly,
    default_snapshots,
    default_source_documents,
)


def test_valid_package_parses_every_table(tmp_path):
    parsed = read_package(build_package(tmp_path / "pkg.zip"))

    assert parsed.manifest_schema_version == "1.0"
    assert parsed.row_counts == {
        "source_documents": 2,
        "snapshots": 3,
        "monthly_values": 4,
        "size_movements": 8,
        "removal_reasons": 2,
        "warnings": 2,
        "conflicts": 1,
        "coverage_rows": 1,
    }
    assert len(parsed.package_sha256) == 64


def test_extracted_prose_is_not_parsed(tmp_path):
    """`raw_reference` is a required column and must never become a field.

    The synthetic package puts a sentence there on purpose. If it ever appears
    on a parsed row, source prose has started leaking into the application.
    """
    parsed = read_package(build_package(tmp_path / "pkg.zip"))
    snapshot = next(row for row in parsed.snapshots if row.snapshot_id == SNAP_A_DIRECT)

    assert not hasattr(snapshot, "raw_reference")
    assert "liiget" not in repr(snapshot)


def test_package_without_root_directory_is_accepted(tmp_path):
    parsed = read_package(build_package(tmp_path / "pkg.zip", root_prefix=""))
    assert parsed.row_counts["source_documents"] == 2


def test_conflict_paths_are_resolved_to_document_ids(tmp_path):
    """A filesystem path must not survive parsing."""
    parsed = read_package(build_package(tmp_path / "pkg.zip"))
    conflict = parsed.conflicts[0]

    assert SOURCE_A in conflict.source_ids
    assert not hasattr(conflict, "source_paths")
    assert PATH_A not in repr(conflict)


def test_year_precision_date_anchors_to_year_end(tmp_path):
    """A bare year is a real shape in the approved package."""
    snapshots = default_snapshots()
    snapshots[2]["observation_date"] = "2024"
    snapshots[2]["observation_date_precision"] = "year"

    parsed = read_package(build_package(tmp_path / "pkg.zip", snapshots=snapshots))
    row = next(row for row in parsed.snapshots if row.observation_date_precision == "year")

    assert row.observation_date.isoformat() == "2024-12-31"


# --------------------------------------------------------------------------
# Manifest integrity
# --------------------------------------------------------------------------


def test_wrong_checksum_is_refused(tmp_path):
    def corrupt(manifest):
        manifest["files"][0]["sha256"] = "0" * 64
        return manifest

    with pytest.raises(PackageContractError, match="kontrollsumma"):
        read_package(build_package(tmp_path / "pkg.zip", mutate_manifest=corrupt))


def test_wrong_size_is_refused(tmp_path):
    def corrupt(manifest):
        manifest["files"][0]["size_bytes"] = 1
        return manifest

    with pytest.raises(PackageContractError, match="suurus"):
        read_package(build_package(tmp_path / "pkg.zip", mutate_manifest=corrupt))


def test_missing_declared_file_is_refused(tmp_path):
    def drop(payloads):
        payloads.pop("data/conflicts.csv")
        return payloads

    # The manifest is built from the payloads, so drop it from the manifest too
    # and the file is then simply absent from a package that requires it.
    with pytest.raises(PackageContractError, match="puudub"):
        read_package(build_package(tmp_path / "pkg.zip", mutate_payloads=drop))


def test_undeclared_extra_file_is_refused(tmp_path):
    """An approved package is exactly the set of files that were approved."""

    def strip_extra(manifest):
        manifest["files"] = [
            entry for entry in manifest["files"] if entry["path"] != "data/extra.csv"
        ]
        return manifest

    def add_extra(payloads):
        payloads["data/extra.csv"] = b"a,b\n1,2\n"
        return payloads

    with pytest.raises(PackageContractError, match="loetlemata"):
        read_package(
            build_package(
                tmp_path / "pkg.zip",
                mutate_payloads=add_extra,
                mutate_manifest=strip_extra,
            )
        )


def test_missing_manifest_is_refused(tmp_path):
    path = tmp_path / "pkg.zip"
    build_package(path)
    stripped = tmp_path / "stripped.zip"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(stripped, "w") as target:
        for info in source.infolist():
            if info.filename.endswith("manifest.json"):
                continue
            target.writestr(info.filename, source.read(info))

    with pytest.raises(PackageContractError):
        read_package(stripped)


def test_unsupported_schema_version_is_refused(tmp_path):
    with pytest.raises(PackageContractError, match="skeemi versioon"):
        read_package(build_package(tmp_path / "pkg.zip", schema_version="9.9"))


# --------------------------------------------------------------------------
# Archive safety
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_name",
    ["../escaped.csv", "/absolute.csv", "data/../../escaped.csv"],
)
def test_path_traversal_is_refused(tmp_path, hostile_name):
    with pytest.raises(PackageContractError, match="failiteed"):
        read_package(build_package(tmp_path / "pkg.zip", raw_members={hostile_name: b"x"}))


def test_symlink_member_is_refused(tmp_path):
    path = tmp_path / "pkg.zip"
    build_package(path)
    with zipfile.ZipFile(path, "a") as archive:
        info = zipfile.ZipInfo("dashkoda-membership-history-import-package/link")
        # 0o120000 is the Unix mode for a symbolic link.
        info.external_attr = (0o120777 & 0xFFFF) << 16
        archive.writestr(info, "/etc/passwd")

    with pytest.raises(PackageContractError, match="nimeviidet"):
        read_package(path)


def test_too_many_members_is_refused(tmp_path):
    with pytest.raises(PackageContractError, match="liiga palju"):
        read_package(build_package(tmp_path / "pkg.zip"), limits=PackageLimits(max_members=3))


def test_oversized_package_is_refused(tmp_path):
    with pytest.raises(PackageContractError, match="liiga suur"):
        read_package(
            build_package(tmp_path / "pkg.zip"), limits=PackageLimits(max_package_bytes=10)
        )


def test_non_zip_is_refused(tmp_path):
    path = tmp_path / "not-a-zip.zip"
    path.write_bytes(b"this is not an archive")

    with pytest.raises(PackageContractError, match="ZIP"):
        read_package(path)


# --------------------------------------------------------------------------
# CSV contract
# --------------------------------------------------------------------------


def test_wrong_header_is_refused(tmp_path):
    def rename_column(payloads):
        payloads["data/conflicts.csv"] = payloads["data/conflicts.csv"].replace(
            b"observation_date", b"observed_date", 1
        )
        return payloads

    with pytest.raises(PackageContractError, match="päis"):
        read_package(build_package(tmp_path / "pkg.zip", mutate_payloads=rename_column))


def test_malformed_number_is_refused(tmp_path):
    snapshots = default_snapshots()
    snapshots[0]["total_members"] = "three thousand"

    with pytest.raises(PackageContractError, match="täisarv"):
        read_package(build_package(tmp_path / "pkg.zip", snapshots=snapshots))


def test_utf8_bom_is_read_identically(tmp_path):
    def add_bom(payloads):
        payloads["data/coverage.csv"] = b"\xef\xbb\xbf" + payloads["data/coverage.csv"]
        return payloads

    parsed = read_package(build_package(tmp_path / "pkg.zip", mutate_payloads=add_bom))
    assert parsed.coverage_rows == 1


def test_duplicate_snapshot_id_is_refused(tmp_path):
    snapshots = default_snapshots()
    snapshots[1]["snapshot_id"] = snapshots[0]["snapshot_id"]

    with pytest.raises(PackageContractError, match="kordub"):
        read_package(build_package(tmp_path / "pkg.zip", snapshots=snapshots))


def test_duplicate_source_document_id_is_refused(tmp_path):
    documents = default_source_documents()
    documents[1]["source_id"] = documents[0]["source_id"]

    with pytest.raises(PackageContractError, match="kordub"):
        read_package(build_package(tmp_path / "pkg.zip", source_documents=documents))


def test_dangling_source_reference_is_refused(tmp_path):
    snapshots = default_snapshots()
    snapshots[0]["source_id"] = "src_does_not_exist"

    with pytest.raises(PackageContractError, match="tundmatule lähtedokumendile"):
        read_package(build_package(tmp_path / "pkg.zip", snapshots=snapshots))


def test_dangling_monthly_selection_is_refused(tmp_path):
    monthly = default_monthly()
    monthly[0]["selected_source_id"] = "src_does_not_exist"

    with pytest.raises(PackageContractError, match="tundmatule lähtedokumendile"):
        read_package(build_package(tmp_path / "pkg.zip", monthly=monthly))


def test_conflict_month_may_not_carry_a_value(tmp_path):
    """The one substitution that would do real damage is refused at the door."""
    monthly = default_monthly()
    monthly[2]["new_members"] = "0"

    with pytest.raises(PackageContractError, match="Vastuolulisel kuul"):
        read_package(build_package(tmp_path / "pkg.zip", monthly=monthly))


def test_month_out_of_range_is_refused(tmp_path):
    monthly = default_monthly()
    monthly[0]["calendar_month"] = "13"

    with pytest.raises(PackageContractError, match="1–12"):
        read_package(build_package(tmp_path / "pkg.zip", monthly=monthly))
