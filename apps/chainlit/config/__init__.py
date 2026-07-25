"""Config package: typed schema + loader for a RAG instance."""

from __future__ import annotations

from config.loader import CONFIG_PATH_ENV, DEFAULT_CONFIG, get_config, load_config
from config.schema import RagConfig

__all__ = [
    "RagConfig",
    "get_config",
    "load_config",
    "CONFIG_PATH_ENV",
    "DEFAULT_CONFIG",
]
