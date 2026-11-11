"""SpecAnvil: spec-driven development bootstrapper and token-saving code-index MCP."""

import os as _os

# Renamed from SpecForge: honour the old environment variable names when the new ones are unset.
for _k, _v in list(_os.environ.items()):
    if _k.startswith("SPECFORGE_") and "SPECANVIL_" + _k[len("SPECFORGE_"):] not in _os.environ:
        _os.environ["SPECANVIL_" + _k[len("SPECFORGE_"):]] = _v

__version__ = "0.10.0"
