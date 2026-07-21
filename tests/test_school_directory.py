import json
from collections import defaultdict
from pathlib import Path

import pytest

import school_directory
from school_directory import (
    AmbiguousSchoolNameError,
    DEFAULT_SCHOOL_CODES_PATH,
    InvalidSchoolNameError,
    SchoolDirectory,
    SchoolDirectoryUnavailableError,
    SchoolNotFoundError,
    normalize_school_name,
)


FIXTURE_SOURCE_REVISION = "a" * 40
FIXTURE_SOURCE_BLOB_SHA = "b" * 40
FIXTURE_SOURCE_SHA256 = "c" * 64


def directory_metadata(*, min_records, record_count):
    return {
        "official_source": "https://official.example.test/schools",
        "mirror_source": (
            "https://github.com/example/schools/blob/{}/school-list.md".format(
                FIXTURE_SOURCE_REVISION
            )
        ),
        "source_url": (
            "https://raw.githubusercontent.com/example/schools/{}/school-list.md".format(
                FIXTURE_SOURCE_REVISION
            )
        ),
        "source_revision": FIXTURE_SOURCE_REVISION,
        "source_blob_sha": FIXTURE_SOURCE_BLOB_SHA,
        "source_sha256": FIXTURE_SOURCE_SHA256,
        "verification_mirror_source": "https://verification.example.test/schools",
        "retrieved_at": "2026-07-21",
        "purpose": "Fixture data for offline directory validation.",
        "mirror_license_note": "Fixture provenance only.",
        "min_records": min_records,
        "record_count": record_count,
    }


def directory_payload(
    records, *, schema_version=1, metadata=None, min_records=None, record_count=None
):
    if metadata is None:
        if min_records is None:
            min_records = max(1, len(records))
        if record_count is None:
            record_count = len(records)
        metadata = directory_metadata(
            min_records=min_records, record_count=record_count
        )
    return {
        "schema_version": schema_version,
        "metadata": metadata,
        "schools": records,
    }


def write_payload(tmp_path, payload):
    data_path = tmp_path / "school_codes.json"
    data_path.write_text(json.dumps(payload), encoding="utf-8")
    return data_path


def write_directory(tmp_path, records, *, min_records=None, record_count=None):
    return write_payload(
        tmp_path,
        directory_payload(
            records, min_records=min_records, record_count=record_count
        ),
    )


@pytest.mark.parametrize("school_name", [None, "", " \u3000 "])
def test_school_name_must_be_a_nonempty_string(school_name):
    with pytest.raises(InvalidSchoolNameError):
        normalize_school_name(school_name)


def test_exact_match_preserves_leading_zero_code(tmp_path):
    directory = SchoolDirectory(
        write_directory(tmp_path, [{"name": "Example University", "code": "001"}])
    )

    assert directory.resolve("Example University") == "001"


def test_nfkc_and_whitespace_normalization_still_requires_exact_match(tmp_path):
    directory = SchoolDirectory(
        write_directory(tmp_path, [{"name": "Example University", "code": "01"}])
    )

    assert directory.resolve("  Ｅｘａｍｐｌｅ\u3000 University  ") == "01"
    with pytest.raises(SchoolNotFoundError):
        directory.resolve("Example")
    with pytest.raises(SchoolNotFoundError):
        directory.resolve("Example University Campus")


def test_same_normalized_name_and_code_is_merged(tmp_path):
    directory = SchoolDirectory(
        write_directory(
            tmp_path,
            [
                {"name": "Example University", "code": "001"},
                {"name": "example  university", "code": "001"},
            ],
        )
    )

    assert directory.resolve("EXAMPLE UNIVERSITY") == "001"


def test_same_normalized_name_with_different_codes_is_ambiguous(tmp_path):
    directory = SchoolDirectory(
        write_directory(
            tmp_path,
            [
                {"name": "Example University", "code": "001"},
                {"name": "example university", "code": "002"},
            ],
        )
    )

    with pytest.raises(AmbiguousSchoolNameError):
        directory.resolve("Example University")


@pytest.mark.parametrize("contents", ["{", "[]", '{"schools": []}'])
def test_missing_or_malformed_directory_fails_safely(tmp_path, contents):
    data_path = tmp_path / "school_codes.json"
    data_path.write_text(contents, encoding="utf-8")

    with pytest.raises(SchoolDirectoryUnavailableError):
        SchoolDirectory(data_path).resolve("Example University")
    with pytest.raises(SchoolDirectoryUnavailableError):
        SchoolDirectory(tmp_path / "missing.json").resolve("Example University")


@pytest.mark.parametrize(
    "payload",
    [
        [{"name": "Example University", "code": "001"}],
        directory_payload(
            [{"name": "Example University", "code": "001"}], schema_version=2
        ),
        directory_payload(
            [{"name": "Example University", "code": "001"}], schema_version=True
        ),
        directory_payload(
            [{"name": "Example University", "code": "001"}], schema_version="1"
        ),
        {
            "schema_version": 1,
            "schools": [{"name": "Example University", "code": "001"}],
        },
        directory_payload(
            [{"name": "Example University", "code": "001"}], metadata=[]
        ),
        directory_payload(
            [{"name": "Example University", "code": "001"}],
            metadata={
                key: value
                for key, value in directory_metadata(
                    min_records=1, record_count=1
                ).items()
                if key != "source_sha256"
            },
        ),
        directory_payload(
            [{"name": "Example University", "code": "001"}],
            metadata={
                key: value
                for key, value in directory_metadata(
                    min_records=1, record_count=1
                ).items()
                if key != "record_count"
            },
        ),
        directory_payload(
            [{"name": "Example University", "code": "001"}], min_records=2
        ),
    ],
    ids=(
        "bare-list-top-level",
        "unknown-schema-version",
        "boolean-schema-version",
        "string-schema-version",
        "missing-metadata",
        "non-object-metadata",
        "missing-source-sha256",
        "missing-record-count",
        "truncated-record-list",
    ),
)
def test_directory_rejects_invalid_top_level_or_metadata(tmp_path, payload):
    with pytest.raises(SchoolDirectoryUnavailableError):
        SchoolDirectory(write_payload(tmp_path, payload)).resolve("Example University")


@pytest.mark.parametrize(
    "record",
    [
        "not-an-object",
        {"name": "Example University"},
        {"code": "001"},
        {"name": "", "code": "001"},
        {"name": "   ", "code": "001"},
        {"name": "Example University", "code": ""},
        {"name": "Example University", "code": " "},
        {"name": 123, "code": "001"},
        {"name": "Example University", "code": 123},
        {"name": "Example University", "code": True},
        {"name": "Example University", "code": "１２３"},
    ],
    ids=(
        "non-object-record",
        "missing-code",
        "missing-name",
        "empty-name",
        "whitespace-name",
        "empty-code",
        "whitespace-code",
        "numeric-name",
        "numeric-code",
        "boolean-code",
        "non-ascii-digit-code",
    ),
)
def test_directory_rejects_invalid_records_safely(tmp_path, record):
    with pytest.raises(SchoolDirectoryUnavailableError):
        SchoolDirectory(write_directory(tmp_path, [record])).resolve(
            "Example University"
        )


@pytest.mark.parametrize("min_records", [0, True, "1"])
def test_directory_requires_a_positive_integer_minimum(tmp_path, min_records):
    with pytest.raises(SchoolDirectoryUnavailableError):
        SchoolDirectory(
            write_directory(
                tmp_path,
                [{"name": "Example University", "code": "001"}],
                min_records=min_records,
            )
        ).resolve("Example University")


@pytest.mark.parametrize(
    ("records", "record_count"),
    [
        ([{"name": "Example University", "code": "001"}], 2),
        (
            [
                {"name": "Example University", "code": "001"},
                {"name": "Second Example University", "code": "002"},
            ],
            1,
        ),
    ],
    ids=("fewer-records-than-declared", "more-records-than-declared"),
)
def test_directory_rejects_record_count_mismatches(
    tmp_path, records, record_count
):
    with pytest.raises(SchoolDirectoryUnavailableError):
        SchoolDirectory(
            write_directory(tmp_path, records, record_count=record_count)
        ).resolve("Example University")


@pytest.mark.parametrize(
    "record_count",
    [None, 0, -1, True, "1", 1.0],
    ids=("none", "zero", "negative", "boolean", "string", "float"),
)
def test_directory_requires_a_positive_integer_record_count(tmp_path, record_count):
    with pytest.raises(SchoolDirectoryUnavailableError):
        SchoolDirectory(
            write_payload(
                tmp_path,
                directory_payload(
                    [{"name": "Example University", "code": "001"}],
                    metadata=directory_metadata(
                        min_records=1, record_count=record_count
                    ),
                ),
            )
        ).resolve("Example University")


def test_directory_is_loaded_once_per_instance(tmp_path, monkeypatch):
    data_path = write_directory(
        tmp_path, [{"name": "Example University", "code": "001"}]
    )
    original_read_text = Path.read_text
    calls = []

    def counting_read_text(path, *args, **kwargs):
        if path == data_path:
            calls.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(school_directory.Path, "read_text", counting_read_text)
    directory = SchoolDirectory(data_path)

    assert directory.resolve("Example University") == "001"
    assert directory.resolve("example university") == "001"
    assert calls == [data_path]


def test_bundled_directory_has_known_codes_and_consistent_records():
    payload = json.loads(DEFAULT_SCHOOL_CODES_PATH.read_text(encoding="utf-8"))
    metadata = payload["metadata"]
    records = payload["schools"]

    assert payload["schema_version"] == 1
    assert len(records) == 358
    assert metadata["record_count"] == 358
    assert len(records) == metadata["record_count"]
    assert metadata["min_records"] >= 300
    assert len(records) >= metadata["min_records"]
    assert metadata["official_source"] == (
        "https://open.17wanxiao.com/kdword_fl02.html"
    )
    assert metadata["mirror_source"] == (
        "https://github.com/zuwei522/perfect-campus_electricity-alert/blob/"
        "f258c6438040f03dbcfb909c7b705c970d976ea3/school-list.md"
    )
    assert metadata["source_url"] == (
        "https://raw.githubusercontent.com/zuwei522/perfect-campus_electricity-alert/"
        "f258c6438040f03dbcfb909c7b705c970d976ea3/school-list.md"
    )
    assert metadata["source_revision"] == "f258c6438040f03dbcfb909c7b705c970d976ea3"
    assert metadata["source_blob_sha"] == "ec9cc46da69666e341072ce1a5274b4aa5cca7ba"
    assert metadata["source_sha256"] == (
        "d1a069ddd4f91235ad1110d42a575bd1774646eb99ab64379796c5b153ba121c"
    )
    assert metadata["verification_mirror_source"].startswith("https://apifox.com/")
    assert all(
        isinstance(record["name"], str)
        and record["name"].strip()
        and isinstance(record["code"], str)
        and record["code"].isascii()
        and record["code"].isdigit()
        for record in records
    )

    codes_by_name = defaultdict(set)
    for record in records:
        codes_by_name[normalize_school_name(record["name"])].add(record["code"])

    assert not [codes for codes in codes_by_name.values() if len(codes) > 1]
    assert {record["name"]: record["code"] for record in records}.items() >= {
        "郑州大学": "11",
        "华东师范大学": "65",
        "西南石油大学": "405",
    }.items()
