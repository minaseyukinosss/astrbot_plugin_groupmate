"""AstrBot Groupmate plugin package."""

import sys as _sys
import types as _types

# AstrBot loads plugins via __import__("data.plugins.<name>.main").
# Register parent namespace packages so deferred relative imports stay stable
# when dependency recovery mutates sys.modules during plugin loading.
_pkg_name = __name__
_parts = _pkg_name.split(".")
for _i in range(1, len(_parts)):
    _ns = ".".join(_parts[:_i])
    if _ns not in _sys.modules:
        _mod = _types.ModuleType(_ns)
        _mod.__path__ = []
        _mod.__package__ = _ns
        _sys.modules[_ns] = _mod
