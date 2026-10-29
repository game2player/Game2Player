"""Construct provider-neutral CUA clients from catalog profiles."""

from __future__ import annotations

from .backends import create_backend
from .client import CUAClient
from .types import CUAClientConfig, ModelProfileLike


def create_client(config: CUAClientConfig) -> CUAClient:
    return CUAClient(config, create_backend(config.profile.backend))


def create_client_config(profile: ModelProfileLike, **runtime: object) -> CUAClientConfig:
    return CUAClientConfig(profile=profile, **runtime)


__all__ = ["create_client", "create_client_config"]
