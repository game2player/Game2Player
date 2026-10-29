"""Unity WebGL inspection and runtime-state tooling.

The package deliberately keeps extracted builds and reverse-engineering output
outside the repository.  Only compact, reviewed profiles belong in
``catalog/unity``.
"""

from .audit import audit_profile, audit_profiles
from .discovery import UnityBuild, discover_build
from .profile import ProfileError, load_profile, validate_profile

__all__ = [
    "ProfileError",
    "UnityBuild",
    "audit_profile",
    "audit_profiles",
    "discover_build",
    "load_profile",
    "validate_profile",
]
