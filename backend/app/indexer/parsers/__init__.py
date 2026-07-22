"""ILanguageParser and the registry that maps a detected language to a
concrete parser. `java/` is the only implementation in this phase; adding a
new language means implementing the interface and registering it — no
change to the scanner, extractors' consumers, or the indexing service.
"""
