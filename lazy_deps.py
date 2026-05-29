"""
Workflow Engine — Lazy Dependency Checks.

Lightweight importability checks for optional dependencies.  Does NOT
auto-install (that's Hermes' job).  Just verifies that a package is
available and raises :class:`FeatureUnavailable` with a helpful
install hint when it's missing.

Usage::

    from .lazy_deps import ensure, FeatureUnavailable

    try:
        ensure("apscheduler")
    except FeatureUnavailable as exc:
        return {"error": str(exc)}

    from apscheduler.triggers.cron import CronTrigger  # safe now
"""

from __future__ import annotations

import importlib


class FeatureUnavailable(RuntimeError):
    """A required optional dependency is not installed."""

    def __init__(self, feature: str, packages: tuple[str, ...]):
        self.feature = feature
        self.packages = packages
        super().__init__(self._format())

    def _format(self) -> str:
        pkg_list = " ".join(self.packages)
        return (
            f"Feature {self.feature!r} unavailable: the required package(s) "
            f"are not installed.  Install with:  pip install {pkg_list}"
        )


# ── Registry ──────────────────────────────────────────────────────────────

# Maps feature keys to the top-level module(s) that must be importable.
_REGISTRY: dict[str, tuple[str, ...]] = {
    "apscheduler": ("apscheduler",),
}


def ensure(feature: str) -> None:
    """Raise :class:`FeatureUnavailable` if *feature* is not importable."""
    modules = _REGISTRY.get(feature)
    if modules is None:
        raise FeatureUnavailable(
            feature, (),
        ) from LookupError(f"Unknown feature {feature!r}")

    missing: list[str] = []
    for mod in modules:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)

    if missing:
        # Derive pip package names from the module names (heuristic)
        pkgs = tuple(_mod_to_pkg(m) for m in missing)
        raise FeatureUnavailable(feature, pkgs)


def _mod_to_pkg(mod: str) -> str:
    """Heuristic: map import name to pip package name."""
    _KNOWN: dict[str, str] = {
        "apscheduler": "apscheduler",
    }
    return _KNOWN.get(mod, mod)
