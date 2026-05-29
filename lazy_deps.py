"""
Workflow Engine — Lazy Dependency Checks.

Lightweight importability checks for optional dependencies.  Raises
:class:`FeatureUnavailable` with a helpful install hint when a
required package is missing.

.. note::

    ``croniter`` is a core Hermes dependency (always installed) — no
    lazy check needed.  Import it directly.

Usage::

    from .lazy_deps import ensure, FeatureUnavailable

    try:
        ensure("some-optional-dep")
    except FeatureUnavailable as exc:
        return {"error": str(exc)}
"""

from __future__ import annotations

import importlib


class FeatureUnavailable(RuntimeError):
    """A required optional dependency is not installed."""

    def __init__(self, feature: str, missing: tuple[str, ...]):
        self.feature = feature
        self.missing = missing
        super().__init__(self._format())

    def _format(self) -> str:
        pkg_list = " ".join(self.missing)
        return (
            f"Feature {self.feature!r} unavailable: the required package(s) "
            f"are not installed.  Install with:  pip install {pkg_list}"
        )


# ── Registry ──────────────────────────────────────────────────────────────

# Maps feature keys to the top-level module(s) that must be importable.
# Package names are derived from module names (same convention as pip).
_REGISTRY: dict[str, tuple[str, ...]] = {
    # Add optional plugin dependencies here as needed.
    # croniter is a core Hermes dep — no entry needed.
}


def ensure(feature: str) -> None:
    """Raise :class:`FeatureUnavailable` if *feature* is not importable.

    Checks every module registered for *feature* via :func:`importlib.import_module`.
    If any module is missing, raises with a ``pip install`` hint.
    """
    modules = _REGISTRY.get(feature)
    if modules is None:
        raise FeatureUnavailable(
            feature,
            (feature,),  # best-effort: use the feature name as package name
        ) from LookupError(f"Unknown feature {feature!r}")

    missing: list[str] = []
    for mod in modules:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)

    if missing:
        raise FeatureUnavailable(feature, tuple(missing))


