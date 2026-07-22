"""Maps a detected language to its parser — the extension point for adding
a new language: implement `ILanguageParser`, add one entry here, done. No
change to the scanner, the indexing service, or the API routers.
"""

from app.indexer.parsers.base import ILanguageParser
from app.indexer.parsers.java.spring_boot_parser import SpringBootJavaParser
from app.indexer.scanner.language_detector import DetectedLanguage

_REGISTRY: dict[DetectedLanguage, ILanguageParser] = {
    DetectedLanguage.JAVA_SPRING_BOOT: SpringBootJavaParser(),
}


def get_parser(language: DetectedLanguage) -> ILanguageParser | None:
    return _REGISTRY.get(language)
