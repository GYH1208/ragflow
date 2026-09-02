"""Read-only SVN connector primitives."""

import csv
import io
import subprocess
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import xxhash

from common.data_source.config import DocumentSource
from common.data_source.exceptions import ConnectorMissingCredentialError, ConnectorValidationError
from common.data_source.models import Document, KeyRecord, SlimDocument

APPROVED_INCLUDE_ROOTS = (
    "1、一级文件",
    "2、二级文件",
    "3、三级文件",
    "4、四级文件",
)
SVN_FILE_INDEX_NAME = "SVN文件索引.csv"


@dataclass(frozen=True)
class SVNEntry:
    relative_path: str
    size: int
    changed_revision: str
    changed_at: str


def _validated_relative_path(value: str) -> PurePosixPath:
    raw_parts = value.split("/")
    if not value or value.startswith("/") or "\\" in value or "\x00" in value or any(part in {".", ".."} for part in raw_parts):
        raise ConnectorValidationError("SVN returned an unsafe relative path.")
    return PurePosixPath(value)


def _is_safe_config_path(value: str) -> bool:
    raw_parts = value.split("/")
    return bool(value and not value.startswith("/") and "\\" not in value and "\x00" not in value and all(part not in {".", ".."} for part in raw_parts))


def select_formal_documents(
    entries: list[SVNEntry],
    *,
    include_roots: set[str],
    excluded_terms: tuple[str, ...],
) -> list[SVNEntry]:
    """Select Word documents per directory, with PDF as the fallback."""
    by_directory: dict[str, list[SVNEntry]] = defaultdict(list)
    for entry in entries:
        path = _validated_relative_path(entry.relative_path)
        if not path.parts or path.parts[0] not in include_roots:
            continue
        if any(term and term in part for part in path.parts for term in excluded_terms):
            continue
        if path.name.startswith("~$"):
            continue
        if path.suffix.lower() not in {".doc", ".docx", ".pdf"}:
            continue
        by_directory[path.parent.as_posix()].append(entry)

    selected: list[SVNEntry] = []
    for directory in sorted(by_directory):
        candidates = by_directory[directory]
        word_documents = [entry for entry in candidates if PurePosixPath(entry.relative_path).suffix.lower() in {".doc", ".docx"}]
        selected.extend(word_documents or candidates)
    return sorted(selected, key=lambda entry: entry.relative_path)


class SVNCommandRunner:
    """Execute SVN commands without exposing credentials in process arguments."""

    def __init__(self, execute: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self._execute = execute

    def run(
        self,
        args: list[str],
        *,
        username: str,
        password: str,
        timeout: int,
    ) -> bytes:
        command = [
            "svn",
            *args,
            "--username",
            username,
            "--non-interactive",
            "--no-auth-cache",
            "--password-from-stdin",
        ]
        result: Any = self._execute(
            command,
            input=(password + "\n").encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
        )
        if result.returncode != 0:
            raise ConnectorValidationError(f"SVN command failed with exit code {result.returncode}.")
        return result.stdout


class SVNConnector:
    """Read documents from an HTTPS SVN repository without mutating it."""

    def __init__(self, config: dict[str, Any], runner: SVNCommandRunner | None = None) -> None:
        repository_url = str(config.get("repository_url") or "").strip().rstrip("/")
        if urlsplit(repository_url).scheme.lower() != "https":
            raise ConnectorValidationError("SVN repository URL must use HTTPS.")

        raw_base_path = str(config.get("base_path") or "").strip()
        raw_include_roots = [str(root).strip() for root in config.get("include_roots") or []]
        if not _is_safe_config_path(raw_base_path) or any(not _is_safe_config_path(root) for root in raw_include_roots):
            raise ConnectorValidationError("SVN configured paths must be safe relative paths.")
        include_roots = [root.rstrip("/") for root in raw_include_roots]
        include_root_set = set(include_roots)
        if not include_roots or len(include_root_set) != len(include_roots) or not include_root_set.issubset(APPROVED_INCLUDE_ROOTS):
            raise ConnectorValidationError("SVN include roots must match the approved hierarchy roots.")

        file_url_base = str(config.get("file_url_base") or "").strip().rstrip("/")
        self.repository_url = repository_url
        self.file_url_base = file_url_base or repository_url
        self.base_path = raw_base_path.rstrip("/")
        self.include_roots = include_roots
        self.excluded_terms = tuple(str(term) for term in config.get("exclude_name_contains") or [])
        credentials = config.get("credentials") or {}
        self.username = str(credentials.get("username") or "")
        self.password = str(credentials.get("password") or "")
        if not self.username or not self.password:
            raise ConnectorMissingCredentialError("SVN")
        self.timeout = int(config.get("timeout") or 60)
        self.batch_size = max(1, int(config.get("batch_size") or 8))
        self.generate_file_index = config.get("generate_file_index") is True
        self.runner = runner or SVNCommandRunner()
        self.repository_uuid: str | None = None
        self.snapshot_revision: str | None = None
        self._listing_cache: dict[str, SVNEntry] = {}
        self._generated_documents: dict[str, Document] = {}

    def _url_for(self, *segments: str) -> str:
        suffix = "/".join(segment.strip("/") for segment in segments if segment)
        return f"{self.repository_url}/{suffix}" if suffix else self.repository_url

    def _file_url_for(self, *segments: str) -> str:
        suffix = "/".join(segment.strip("/") for segment in segments if segment)
        return f"{self.file_url_base}/{suffix}" if suffix else self.file_url_base

    def _run(self, args: list[str]) -> bytes:
        return self.runner.run(
            args,
            username=self.username,
            password=self.password,
            timeout=self.timeout,
        )

    def _read_snapshot_identity(self) -> tuple[str, str]:
        output = self._run(["info", "--xml", self._url_for(self.base_path)])
        try:
            root = ElementTree.fromstring(output)
            entry = root.find("entry")
            uuid_node = root.find("./entry/repository/uuid")
            if entry is None or uuid_node is None or not uuid_node.text:
                raise ValueError("missing repository identity")
            revision = entry.attrib["revision"]
        except (ElementTree.ParseError, KeyError, ValueError) as exc:
            raise ConnectorValidationError("SVN info returned invalid XML.") from exc
        return uuid_node.text.strip(), revision

    @staticmethod
    def _fingerprint(repository_uuid: str, entry: SVNEntry) -> str:
        return xxhash.xxh128(f"{repository_uuid}:{entry.relative_path}:{entry.changed_revision}:{entry.size}".encode()).hexdigest()

    @staticmethod
    def _parse_listing(output: bytes, include_root: str) -> list[SVNEntry]:
        try:
            root = ElementTree.fromstring(output)
        except ElementTree.ParseError as exc:
            raise ConnectorValidationError("SVN list returned invalid XML.") from exc

        entries: list[SVNEntry] = []
        for node in root.findall("./list/entry"):
            if node.attrib.get("kind") != "file":
                continue
            name = (node.findtext("name") or "").strip()
            commit = node.find("commit")
            if not name or commit is None:
                continue
            relative_path = PurePosixPath(include_root, name).as_posix()
            entries.append(
                SVNEntry(
                    relative_path=relative_path,
                    size=int(node.findtext("size") or 0),
                    changed_revision=commit.attrib.get("revision", ""),
                    changed_at=(commit.findtext("date") or "").strip(),
                )
            )
        return entries

    def list_keys(self):
        repository_uuid, revision = self._read_snapshot_identity()
        self.repository_uuid = repository_uuid
        self.snapshot_revision = revision
        self._listing_cache = {}
        self._generated_documents = {}

        entries: list[SVNEntry] = []
        for include_root in self.include_roots:
            output = self._run(
                [
                    "list",
                    "--xml",
                    "--recursive",
                    "-r",
                    revision,
                    self._url_for(self.base_path, include_root),
                ]
            )
            entries.extend(self._parse_listing(output, include_root))

        selected = select_formal_documents(
            entries,
            include_roots=set(self.include_roots),
            excluded_terms=self.excluded_terms,
        )
        for entry in selected:
            key = f"{repository_uuid}:{entry.relative_path}"
            fingerprint = self._fingerprint(repository_uuid, entry)
            self._listing_cache[key] = entry
            yield KeyRecord(key=key, fingerprint=fingerprint)

        if self.generate_file_index:
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(["文件名", "SVN完整路径"])
            for entry in selected:
                writer.writerow(
                    [
                        PurePosixPath(entry.relative_path).name,
                        self._file_url_for(self.base_path, entry.relative_path),
                    ]
                )
            blob = output.getvalue().encode("utf-8")
            key = f"{repository_uuid}:{SVN_FILE_INDEX_NAME}"
            fingerprint = xxhash.xxh128(blob).hexdigest()
            self._generated_documents[key] = Document(
                id=key,
                source=DocumentSource.SVN,
                semantic_identifier=SVN_FILE_INDEX_NAME,
                extension=".csv",
                blob=blob,
                doc_updated_at=datetime.now(UTC),
                size_bytes=len(blob),
                relative_path=SVN_FILE_INDEX_NAME,
                fingerprint=fingerprint,
            )
            yield KeyRecord(key=key, fingerprint=fingerprint)

    def get_value(self, key: str) -> Document:
        if key in self._generated_documents:
            return self._generated_documents[key]

        entry = self._listing_cache.get(key)
        if entry is None or self.repository_uuid is None or self.snapshot_revision is None:
            raise KeyError(f"get_value({key!r}) called before list_keys() yielded the key, or after a subsequent list_keys() reset the cache")

        blob = self._run(
            [
                "cat",
                "-r",
                self.snapshot_revision,
                self._url_for(self.base_path, entry.relative_path),
            ]
        )
        path = PurePosixPath(entry.relative_path)
        try:
            commit_date = entry.changed_at
            if commit_date.endswith("Z"):
                commit_date = f"{commit_date[:-1]}+00:00"
            updated_at = datetime.fromisoformat(commit_date)
        except ValueError as exc:
            raise ConnectorValidationError("SVN list returned an invalid commit date.") from exc
        return Document(
            id=key,
            source=DocumentSource.SVN,
            semantic_identifier=path.name,
            extension=path.suffix.lower(),
            blob=blob,
            doc_updated_at=updated_at,
            size_bytes=len(blob),
            relative_path=entry.relative_path,
            fingerprint=self._fingerprint(self.repository_uuid, entry),
        )

    def retrieve_all_slim_docs_perm_sync(self, callback: Any = None):
        del callback
        batch: list[SlimDocument] = []
        for key_record in self.list_keys():
            batch.append(SlimDocument(id=key_record.key))
            if len(batch) >= self.batch_size:
                yield batch
                batch = []
        if batch:
            yield batch

    def validate_connector_settings(self) -> dict[str, Any]:
        documents = list(self.list_keys())
        return {
            "repository_uuid": self.repository_uuid,
            "revision": self.snapshot_revision,
            "roots": len(self.include_roots),
            "documents": len(documents),
        }
