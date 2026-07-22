"""Java/Spring Boot: walks every `.java` file with tree-sitter, runs each
extractor against it, and merges the results with `pom.xml`'s dependencies
into one ArchitectureModel. No AI, no heuristics beyond annotation/structure
matching — fully deterministic.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from app.indexer.extractors.controllers import extract_controllers
from app.indexer.extractors.feign_clients import extract_feign_clients
from app.indexer.extractors.kafka import extract_kafka_consumers, extract_kafka_producers
from app.indexer.extractors.services import extract_services
from app.indexer.models.architecture import ArchitectureModel
from app.indexer.parsers.base import ILanguageParser
from app.indexer.parsers.java.pom_parser import parse_maven_dependencies

logger = logging.getLogger(__name__)

_JAVA_LANGUAGE = Language(tsjava.language())

# Build output and dependency caches - never source the repository owner
# actually wrote, and often far larger than the real source tree.
_SKIP_DIRECTORIES = {"target", "build", ".git", "node_modules", ".idea"}


def _iter_java_files(repo_root: Path) -> Iterator[Path]:
    for path in repo_root.rglob("*.java"):
        if any(part in _SKIP_DIRECTORIES for part in path.parts):
            continue
        yield path


class SpringBootJavaParser(ILanguageParser):
    def __init__(self) -> None:
        self._parser = Parser(_JAVA_LANGUAGE)

    def parse(self, repo_root: Path) -> ArchitectureModel:
        model = ArchitectureModel(language="java", framework="spring-boot")

        pom_path = repo_root / "pom.xml"
        if pom_path.is_file():
            model.maven_dependencies = parse_maven_dependencies(pom_path)

        for java_file in _iter_java_files(repo_root):
            relative_path = str(java_file.relative_to(repo_root))
            source = self._read_source(java_file, relative_path)
            if source is None:
                continue

            root = self._parser.parse(source).root_node
            model.controllers.extend(extract_controllers(root, source, relative_path))
            model.services.extend(extract_services(root, source, relative_path))
            model.feign_clients.extend(extract_feign_clients(root, source, relative_path))
            model.kafka_consumers.extend(extract_kafka_consumers(root, source, relative_path))
            model.kafka_producers.extend(extract_kafka_producers(root, source, relative_path))

        return model

    @staticmethod
    def _read_source(java_file: Path, relative_path: str) -> bytes | None:
        # A best-effort scanner over a repository it doesn't control the
        # contents of: one unreadable file (odd encoding, permissions)
        # shouldn't abort indexing everything else.
        try:
            return java_file.read_bytes()
        except OSError:
            logger.warning("Skipping unreadable file: %s", relative_path, exc_info=True)
            return None
