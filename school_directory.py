"""Resolve Wanxiao school names from the bundled offline directory."""

import json
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

DEFAULT_SCHOOL_CODES_PATH = Path(__file__).with_name("school_codes.json")
SUPPORTED_SCHEMA_VERSION = 1
_REQUIRED_METADATA_TEXT_FIELDS = (
    "official_source",
    "mirror_source",
    "source_url",
    "source_revision",
    "source_blob_sha",
    "source_sha256",
    "verification_mirror_source",
    "retrieved_at",
    "purpose",
    "mirror_license_note",
)


class SchoolDirectoryError(ValueError):
    """Base class for safe failures while resolving a configured school name."""


class InvalidSchoolNameError(SchoolDirectoryError):
    """The configured school name is not a usable non-empty string."""


class SchoolNotFoundError(SchoolDirectoryError):
    """The normalized school name is not present in the bundled directory."""


class AmbiguousSchoolNameError(SchoolDirectoryError):
    """The normalized school name maps to more than one school code."""


class SchoolDirectoryUnavailableError(SchoolDirectoryError):
    """The bundled directory cannot be read or fails validation."""


def normalize_school_name(value: Any) -> str:
    """Normalize a school name without applying any fuzzy matching rules."""
    if not isinstance(value, str):
        raise InvalidSchoolNameError()

    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.split()).casefold()
    if not normalized or any(
        unicodedata.category(character) == "Cc" for character in normalized
    ):
        raise InvalidSchoolNameError()
    return normalized


class SchoolDirectory:
    """Lazily load and exactly resolve a bundled school-name to code table."""

    def __init__(self, data_path: Path = DEFAULT_SCHOOL_CODES_PATH):
        self._data_path = Path(data_path)

    @staticmethod
    def _is_ascii_hex(value: str, length: int) -> bool:
        return (
            len(value) == length
            and value.isascii()
            and all(character in "0123456789abcdefABCDEF" for character in value)
        )

    @classmethod
    def _validate_metadata(cls, metadata: Any) -> Tuple[int, int]:
        if not isinstance(metadata, dict):
            raise SchoolDirectoryUnavailableError()

        values = {}
        for field in _REQUIRED_METADATA_TEXT_FIELDS:
            value = metadata.get(field)
            if not isinstance(value, str) or not value or value != value.strip():
                raise SchoolDirectoryUnavailableError()
            values[field] = value

        if (
            not cls._is_ascii_hex(values["source_revision"], 40)
            or not cls._is_ascii_hex(values["source_blob_sha"], 40)
            or not cls._is_ascii_hex(values["source_sha256"], 64)
            or values["source_revision"] not in values["mirror_source"]
            or values["source_revision"] not in values["source_url"]
        ):
            raise SchoolDirectoryUnavailableError()

        min_records = metadata.get("min_records")
        if type(min_records) is not int or min_records < 1:
            raise SchoolDirectoryUnavailableError()

        record_count = metadata.get("record_count")
        if type(record_count) is not int or record_count < 1:
            raise SchoolDirectoryUnavailableError()
        return min_records, record_count

    @lru_cache(maxsize=None)
    def _load_codes(self) -> Dict[str, Tuple[str, ...]]:
        try:
            payload = json.loads(self._data_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SchoolDirectoryUnavailableError() from error

        if not isinstance(payload, dict):
            raise SchoolDirectoryUnavailableError()
        if (
            type(payload.get("schema_version")) is not int
            or payload["schema_version"] != SUPPORTED_SCHEMA_VERSION
        ):
            raise SchoolDirectoryUnavailableError()

        min_records, record_count = self._validate_metadata(payload.get("metadata"))
        records = payload.get("schools")
        if (
            not isinstance(records, list)
            or len(records) < min_records
            or len(records) != record_count
        ):
            raise SchoolDirectoryUnavailableError()

        codes_by_name = defaultdict(set)
        for record in records:
            if not isinstance(record, dict):
                raise SchoolDirectoryUnavailableError()
            name = record.get("name")
            code = record.get("code")
            if (
                not isinstance(code, str)
                or not code
                or code != code.strip()
                or not code.isascii()
                or not code.isdigit()
            ):
                raise SchoolDirectoryUnavailableError()
            try:
                normalized_name = normalize_school_name(name)
            except InvalidSchoolNameError as error:
                raise SchoolDirectoryUnavailableError() from error
            codes_by_name[normalized_name].add(code)

        return {
            normalized_name: tuple(sorted(codes))
            for normalized_name, codes in codes_by_name.items()
        }

    def resolve(self, school_name: Any) -> str:
        """Return a unique code for one normalized, exact school-name match."""
        normalized_name = normalize_school_name(school_name)
        codes = self._load_codes().get(normalized_name)
        if not codes:
            raise SchoolNotFoundError()
        if len(codes) != 1:
            raise AmbiguousSchoolNameError()
        return codes[0]


__all__ = [
    "AmbiguousSchoolNameError",
    "DEFAULT_SCHOOL_CODES_PATH",
    "InvalidSchoolNameError",
    "SchoolDirectory",
    "SchoolDirectoryError",
    "SchoolDirectoryUnavailableError",
    "SchoolNotFoundError",
    "normalize_school_name",
]
