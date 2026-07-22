"""The contract every language/framework parser implements.

Adding a new language later means: implement this, add one line to
`registry.py`. No change to the scanner, the indexing service, or the API.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from app.indexer.models.architecture import ArchitectureModel


class ILanguageParser(ABC):
    @abstractmethod
    def parse(self, repo_root: Path) -> ArchitectureModel:
        """Parse an already-cloned repository rooted at `repo_root` and
        return a fully-populated ArchitectureModel."""
        raise NotImplementedError
