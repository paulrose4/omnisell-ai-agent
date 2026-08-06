from __future__ import annotations

import warnings
from enum import StrEnum


class ReleaseProfile(StrEnum):
    FULL_DEVELOPMENT = "full_development"
    GOLDEN_DEMO = "golden_demo"
    READ_ONLY_PILOT = "read_only_pilot"


LEGACY_RELEASE_PROFILE_REMOVAL_VERSION = "2.0.0"
_LEGACY_PROFILE_ALIASES = {
    "full": ReleaseProfile.FULL_DEVELOPMENT,
    "interview": ReleaseProfile.GOLDEN_DEMO,
}


def normalize_release_profile(value: object) -> ReleaseProfile:
    if isinstance(value, ReleaseProfile):
        return value
    normalized = str(value).strip().lower()
    legacy = _LEGACY_PROFILE_ALIASES.get(normalized)
    if legacy is not None:
        warnings.warn(
            f"Release profile {normalized!r} is deprecated and will be removed in "
            f"{LEGACY_RELEASE_PROFILE_REMOVAL_VERSION}; use {legacy.value!r}.",
            DeprecationWarning,
            stacklevel=3,
        )
        return legacy
    try:
        return ReleaseProfile(normalized)
    except ValueError as exc:
        supported = ", ".join(profile.value for profile in ReleaseProfile)
        raise ValueError(f"RELEASE_PROFILE must be one of: {supported}") from exc
