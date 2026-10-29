"""HTTPS transport for repository Codex calls using the ChatGPT login."""

import json


def chatgpt_https_args(*, single_attempt: bool = False) -> list[str]:
    # A separate provider is necessary: the built-in OpenAI provider does not
    # accept a supports_websockets override. Authentication stays with Codex.
    provider = "game2player_https"
    settings = {
        "model_provider": provider,
        f"model_providers.{provider}.name": "OpenAI HTTPS",
        f"model_providers.{provider}.base_url": "https://chatgpt.com/backend-api/codex",
        f"model_providers.{provider}.wire_api": "responses",
        f"model_providers.{provider}.requires_openai_auth": True,
        f"model_providers.{provider}.supports_websockets": False,
    }
    if single_attempt:
        # CUAClient owns retries of a decision; do not multiply them inside CLI.
        settings[f"model_providers.{provider}.request_max_retries"] = 0
        settings[f"model_providers.{provider}.stream_max_retries"] = 0
    arguments = ["--disable", "unbounded_connection_retries"]
    for key, value in settings.items():
        arguments.extend(["-c", f"{key}={json.dumps(value)}"])
    return arguments
