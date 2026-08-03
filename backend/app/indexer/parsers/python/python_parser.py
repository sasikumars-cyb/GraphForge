"""Python: walks every `.py` file with tree-sitter, extracts imports/
classes/functions, and merges the result with `pyproject.toml`/
`requirements.txt` dependencies into one ArchitectureModel. Mirrors
`SpringBootJavaParser`'s shape exactly - no AI, fully deterministic.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from app.indexer.extractors.python.classes import extract_module_classes
from app.indexer.extractors.python.functions import extract_module_functions
from app.indexer.extractors.python.imports import extract_imports
from app.indexer.extractors.python.kafka import extract_kafka_consumers, extract_kafka_producers
from app.indexer.extractors.python.spark import (
    extract_spark_table_reads,
    extract_spark_table_writes,
)
from app.indexer.models.architecture import ArchitectureModel, PythonModule, SourceLocation
from app.indexer.parsers.base import ILanguageParser
from app.indexer.parsers.python.dependency_parser import parse_python_dependencies

logger = logging.getLogger(__name__)

_PYTHON_LANGUAGE = Language(tspython.language())

_SKIP_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "build",
    "dist",
    "site-packages",
}


def _iter_python_files(repo_root: Path) -> Iterator[Path]:
    for path in repo_root.rglob("*.py"):
        if any(part in _SKIP_DIRECTORIES for part in path.parts):
            continue
        yield path


def _module_and_package_name(relative_path: Path) -> tuple[str, str]:
    """`app/services/workflow_service.py` -> ("app.services.workflow_service",
    "app.services"). `app/__init__.py` -> ("app", "")."""
    parts = list(relative_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    module_name = ".".join(parts)
    package_name = ".".join(parts[:-1])
    return module_name, package_name


class PythonParser(ILanguageParser):
    def __init__(self) -> None:
        self._parser = Parser(_PYTHON_LANGUAGE)

    def parse(self, repo_root: Path) -> ArchitectureModel:
        model = ArchitectureModel(language="python", framework=None)
        model.python_dependencies = parse_python_dependencies(repo_root)

        for python_file in _iter_python_files(repo_root):
            relative_path = python_file.relative_to(repo_root)
            source = self._read_source(python_file, str(relative_path))
            if source is None:
                continue

            root = self._parser.parse(source).root_node
            module_name, package_name = _module_and_package_name(relative_path)
            model.python_modules.append(
                PythonModule(
                    name=module_name,
                    package=package_name,
                    location=SourceLocation(file_path=str(relative_path)),
                    imports=extract_imports(root, source, str(relative_path)),
                    classes=extract_module_classes(root, source, str(relative_path)),
                    functions=extract_module_functions(root, source, str(relative_path)),
                )
            )
            model.spark_table_reads.extend(
                extract_spark_table_reads(root, source, str(relative_path))
            )
            model.spark_table_writes.extend(
                extract_spark_table_writes(root, source, str(relative_path))
            )
            model.kafka_producers.extend(
                extract_kafka_producers(root, source, str(relative_path), module_name)
            )
            model.kafka_consumers.extend(
                extract_kafka_consumers(root, source, str(relative_path), module_name)
            )

        return model

    @staticmethod
    def _read_source(python_file: Path, relative_path: str) -> bytes | None:
        try:
            return python_file.read_bytes()
        except OSError:
            logger.warning("Skipping unreadable file: %s", relative_path, exc_info=True)
            return None
