import os
from pathlib import Path
from urllib.parse import urlparse

from crewai import LLM
from dotenv import dotenv_values, load_dotenv
import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOTENV_PATH = PROJECT_ROOT / ".env"
INITIAL_PROCESS_ENV = {
    "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
    "OPENAI_API_BASE": os.environ.get("OPENAI_API_BASE"),
    "OPENAI_MODEL_NAME": os.environ.get("OPENAI_MODEL_NAME"),
}


def _load_project_env() -> dict[str, str]:
    """Load the project .env and override conflicting process env variables."""

    if not DOTENV_PATH.exists():
        return {}

    load_dotenv(dotenv_path=DOTENV_PATH, override=True)

    return {
        key: value
        for key, value in dotenv_values(DOTENV_PATH).items()
        if isinstance(value, str)
    }


def _redact_secret(secret: str) -> str:
    if not secret:
        return "(missing)"
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:6]}...{secret[-4:]}"


def get_default_llm() -> LLM:
    """Build the default LLM from environment variables."""

    _load_project_env()

    llm_kwargs = {
        "model": os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini"),
    }

    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        llm_kwargs["api_key"] = api_key

    base_url = os.getenv("OPENAI_API_BASE")
    if base_url:
        llm_kwargs["base_url"] = base_url

    return LLM(**llm_kwargs)


def get_llm_runtime_info() -> dict[str, str]:
    """Return a safe summary of the active LLM configuration."""

    dotenv_config = _load_project_env()

    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
    model_name = os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")
    hostname = urlparse(base_url).hostname or ""

    if hostname in {"api.uniapi.io", "api.uniapi.ai"}:
        provider = "UniAPI (OpenAI-compatible)"
    elif hostname == "api.openai.com":
        provider = "OpenAI"
    elif hostname in {"localhost", "127.0.0.1"}:
        provider = "Local OpenAI-compatible provider"
    elif hostname:
        provider = "Custom OpenAI-compatible provider"
    else:
        provider = "Unknown provider"

    if not api_key:
        auth_status = "missing OPENAI_API_KEY"
    elif api_key == "your-uniapi-key-here":
        auth_status = "placeholder OPENAI_API_KEY"
    else:
        auth_status = "OPENAI_API_KEY is set"

    sources = {
        "OPENAI_API_KEY": ".env" if "OPENAI_API_KEY" in dotenv_config else "process environment",
        "OPENAI_API_BASE": ".env" if "OPENAI_API_BASE" in dotenv_config else "process environment",
        "OPENAI_MODEL_NAME": ".env" if "OPENAI_MODEL_NAME" in dotenv_config else "process environment",
    }

    overridden_keys = [
        key
        for key, dotenv_value in dotenv_config.items()
        if key in INITIAL_PROCESS_ENV
        and INITIAL_PROCESS_ENV.get(key)
        and INITIAL_PROCESS_ENV.get(key) != dotenv_value
    ]

    override_note = (
        ".env overrides process env for " + ", ".join(sorted(overridden_keys))
        if overridden_keys
        else "no conflicting process env detected"
    )

    return {
        "provider": provider,
        "base_url": base_url,
        "api_key": _redact_secret(api_key),
        "model": model_name,
        "auth_status": auth_status,
        "api_key_source": sources["OPENAI_API_KEY"],
        "base_url_source": sources["OPENAI_API_BASE"],
        "model_source": sources["OPENAI_MODEL_NAME"],
        "override_note": override_note,
    }


def format_llm_runtime_info() -> str:
    """Format a human-readable startup summary for the active LLM config."""

    info = get_llm_runtime_info()
    return "\n".join(
        [
            "LLM diagnostics:",
            f"  provider: {info['provider']}",
            f"  base_url: {info['base_url']} ({info['base_url_source']})",
            f"  api_key: {info['api_key']} ({info['api_key_source']})",
            f"  model: {info['model']} ({info['model_source']})",
            f"  auth: {info['auth_status']}",
            f"  source: {info['override_note']}",
        ]
    )


def validate_llm_connection() -> None:
    """Fail fast with an actionable message when LLM auth/config is invalid."""

    info = get_llm_runtime_info()

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = info["base_url"]
    model_name = info["model"]

    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in .env.")

    if api_key == "your-uniapi-key-here":
        raise RuntimeError("OPENAI_API_KEY is still the placeholder value in .env.")

    response = httpx.get(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=20.0,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "LLM authentication failed. UniAPI rejected OPENAI_API_KEY with 401. "
            f"Check the token and confirm it is allowed to access model '{model_name}'."
        )

    if response.status_code >= 400:
        detail = response.text.strip().replace("\n", " ")[:200]
        raise RuntimeError(
            f"LLM preflight failed against {base_url}/models with status {response.status_code}: {detail}"
        )