"""Read-only, bounded inspection of captured game assets and ZIP shards."""

from .assets import Asset, has_marker, iter_assets

__all__ = ["Asset", "has_marker", "iter_assets"]
