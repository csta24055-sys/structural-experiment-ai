from .base import Source, SourceChanged, SourceError
from .local import LocalSource


def make_source(cfg) -> Source:
    t = cfg.data["source"]["type"]
    if t == "local":
        return LocalSource(cfg.data["source"]["path"])
    from .dropbox_source import DropboxSource
    return DropboxSource(cfg.data["source"]["path"])


__all__ = ["Source", "SourceChanged", "SourceError", "LocalSource", "make_source"]
