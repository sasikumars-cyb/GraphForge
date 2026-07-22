"""Orchestrates the full indexing pipeline: clone -> detect -> parse ->
build graph -> persist -> clean up temp files, regardless of success or
failure.
"""
