"""Model client factory for creating Nano-Codex chat clients.

This module provides a factory for creating model clients based on configuration.

For new code, use:
    from src.utils.model_client import create_chat_client, get_model_config
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from src.agent_framework_patch import NanoOpenAIChatCompletionClient


def _resolve_config_path(config_path: Optional[str | Path] = None) -> Path:
    """Resolve the effective model config path used by manager caching."""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "configs" / "model_config.json"
    return Path(config_path).expanduser().resolve()


@dataclass
class ModelConfig:
    """Configuration for a specific model."""

    model_id: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class ModelConfigManager:
    """Manager for model configurations and client creation."""

    def __init__(self, config_path: Optional[str | Path] = None):
        """Initialize the model config manager.

        Args:
            config_path: Path to model_config.json. If None, uses default location.
        """
        self.config_path = _resolve_config_path(config_path)
        self._settings: Dict[str, Any] = {}
        self._load_settings()

    def _load_settings(self) -> None:
        """Load settings from JSON file."""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Model config file not found: {self.config_path}\n"
                "Please create configs/model_config.json with your model configurations."
            )

        with open(self.config_path, "r", encoding="utf-8") as f:
            self._settings = json.load(f)

    def reload(self) -> None:
        """Reload settings from file."""
        self._load_settings()

    def get_model_config(self, model: str) -> ModelConfig:
        """Get configuration for a specific model.

        Args:
            model: Name of the model

        Returns:
            ModelConfig with all settings

        Raises:
            ValueError: If model is not found in settings
        """
        # Check if model exists in settings
        models = self._settings.get("models", {})
        if model not in models:
            raise ValueError(
                f"Model '{model}' not found in {self.config_path}.\n"
                f"Available models: {list(models.keys())}"
            )

        model_config = models[model]
        global_config = self._settings.get("global", {})

        # Merge global and model-specific settings (model-specific overrides global)
        return ModelConfig(
            model_id=model_config.get("model_id", model),
            base_url=model_config.get("base_url", global_config.get("base_url")),
            api_key=model_config.get("api_key", global_config.get("api_key")),
        )

    def create_client(
        self,
        model: str,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        instruction_role: Optional[str] = None,
        **kwargs,
    ) -> NanoOpenAIChatCompletionClient:
        """Create a Nano-Codex Chat Completions client for the specified model.

        Args:
            model: Name of the model
            base_url: Optional base URL (overrides config)
            api_key: Optional API key (overrides config)
            instruction_role: The role to use for 'instruction' messages (default: "system")
            **kwargs: Additional arguments to pass to the client constructor
                (e.g. middleware, compaction_strategy, tokenizer)

        Returns:
            Configured NanoOpenAIChatCompletionClient

        Examples:
            >>> manager = ModelConfigManager()
            >>> client = manager.create_client("deepseek-r1")
            >>> # Or override settings:
            >>> client = manager.create_client(
            ...     "gpt-4o",
            ...     base_url="https://custom.api.com",
            ...     api_key="custom-key"
            ... )
        """
        config = self.get_model_config(model)
        kwargs.pop("auto_compact_config", None)

        # Use provided values or fall back to config
        final_base_url = config.base_url if base_url is None else base_url
        final_api_key = config.api_key if api_key is None else api_key

        # Build client arguments
        client_kwargs = {
            "model": config.model_id,
        }

        # Only add base_url and api_key if they are not None
        if final_base_url is not None:
            client_kwargs["base_url"] = final_base_url
        if final_api_key is not None:
            client_kwargs["api_key"] = final_api_key
        if instruction_role is not None:
            client_kwargs["instruction_role"] = instruction_role

        # Add any additional kwargs
        client_kwargs.update(kwargs)

        return NanoOpenAIChatCompletionClient(**client_kwargs)

    def list_models(self) -> list[str]:
        """List all available model names.

        Returns:
            List of model names from settings
        """
        return list(self._settings.get("models", {}).keys())


_MANAGER_CACHE: Dict[Path, ModelConfigManager] = {}


def get_model_config_manager(config_path: Optional[str | Path] = None) -> ModelConfigManager:
    """Get a cached ``ModelConfigManager`` for the resolved config path.

    Args:
        config_path: Optional path to config file.

    Returns:
        Cached ModelConfigManager instance for that config file.
    """
    resolved_path = _resolve_config_path(config_path)
    manager = _MANAGER_CACHE.get(resolved_path)
    if manager is None:
        manager = ModelConfigManager(resolved_path)
        _MANAGER_CACHE[resolved_path] = manager
    return manager


def create_chat_client(model: str, **kwargs) -> NanoOpenAIChatCompletionClient:
    """Convenience function to create a model client.

    This is a shorthand for get_model_config_manager().create_client(model, **kwargs)

    Args:
        model: Name of the model
        **kwargs: Additional arguments passed to create_client
            (e.g. middleware, instruction_role, compaction_strategy)

    Returns:
        Configured NanoOpenAIChatCompletionClient

    Examples:
        >>> from src.utils.model_client import create_chat_client
        >>> client = create_chat_client("deepseek-r1")
    """
    config_path = kwargs.pop("config_path", None)
    manager = get_model_config_manager(config_path)
    return manager.create_client(model, **kwargs)


def get_model_config(model: str) -> ModelConfig:
    """Convenience function to get model config.

    Args:
        model: Name of the model

    Returns:
        ModelConfig with model settings
    """
    manager = get_model_config_manager()
    return manager.get_model_config(model)


__all__ = [
    "ModelConfig",
    "ModelConfigManager",
    "get_model_config_manager",
    "create_chat_client",
    "get_model_config",
]
