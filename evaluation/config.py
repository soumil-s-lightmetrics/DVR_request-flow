"""
Configuration management for RAG evaluation.
"""

import os
import yaml
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FleetConfig:
    """Fleet-specific configuration."""
    name: str
    fleet_portal_version: str
    device_apk_version: str
    camera_models: List[str]
    disabled_standard_events: List[str]
    plan: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format expected by handlers."""
        return {
            "fleet_portal_version": self.fleet_portal_version,
            "device_apk_version": self.device_apk_version,
            "camera_models": self.camera_models,
            "disabled_standard_events": self.disabled_standard_events,
            "plan": self.plan
        }


@dataclass
class HandlerConfig:
    """Handler-specific configuration."""
    name: str
    enabled: bool
    class_name: str
    module: str
    fleet_configs: List[Optional[str]]


@dataclass
class RagasConfig:
    """Ragas-specific configuration."""
    llm_model: str
    llm_temperature: float
    llm_max_tokens: int
    embeddings_model: str


@dataclass
class EvaluationConfig:
    """Main evaluation configuration."""
    handlers: Dict[str, HandlerConfig] = field(default_factory=dict)
    fleet_configs: Dict[str, FleetConfig] = field(default_factory=dict)
    metrics: List[str] = field(default_factory=list)
    golden_questions_path: str = ""
    ground_truth_path: str = ""
    collected_data_dir: str = ""
    results_dir: str = ""
    timestamp_runs: bool = True
    save_formats: List[str] = field(default_factory=list)
    ragas: Optional[RagasConfig] = None
    collection_max_retries: int = 3
    collection_retry_delay: int = 2
    collection_timeout: int = 60
    save_incremental: bool = True
    logging_level: str = "INFO"

    @classmethod
    def from_yaml(cls, config_path: str) -> "EvaluationConfig":
        """Load configuration from YAML file."""
        with open(config_path, 'r') as f:
            config_data = yaml.safe_load(f)

        # Parse fleet configs
        fleet_configs = {}
        for name, fleet_data in config_data.get("fleet_configs", {}).items():
            fleet_configs[name] = FleetConfig(
                name=name,
                fleet_portal_version=fleet_data["fleet_portal_version"],
                device_apk_version=fleet_data["device_apk_version"],
                camera_models=fleet_data["camera_models"],
                disabled_standard_events=fleet_data["disabled_standard_events"],
                plan=fleet_data["plan"]
            )

        # Parse handler configs
        handlers = {}
        for name, handler_data in config_data.get("handlers", {}).items():
            handlers[name] = HandlerConfig(
                name=name,
                enabled=handler_data.get("enabled", True),
                class_name=handler_data["class_name"],
                module=handler_data["module"],
                fleet_configs=handler_data.get("fleet_configs", [])
            )

        # Parse ragas config
        ragas_data = config_data.get("ragas", {})
        ragas_config = RagasConfig(
            llm_model=ragas_data.get("llm", {}).get("model", "gpt-4o-mini"),
            llm_temperature=ragas_data.get("llm", {}).get("temperature", 0),
            llm_max_tokens=ragas_data.get("llm", {}).get("max_tokens", 1000),
            embeddings_model=ragas_data.get("embeddings", {}).get("model", "text-embedding-3-small")
        )

        # Parse data paths
        data_config = config_data.get("data", {})
        output_config = config_data.get("output", {})
        collection_config = config_data.get("collection", {})
        logging_config = config_data.get("logging", {})

        return cls(
            handlers=handlers,
            fleet_configs=fleet_configs,
            metrics=config_data.get("metrics", []),
            golden_questions_path=data_config.get("golden_questions", ""),
            ground_truth_path=data_config.get("ground_truth", ""),
            collected_data_dir=data_config.get("collected_data_dir", ""),
            results_dir=output_config.get("results_dir", ""),
            timestamp_runs=output_config.get("timestamp_runs", True),
            save_formats=output_config.get("save_formats", ["json", "csv"]),
            ragas=ragas_config,
            collection_max_retries=collection_config.get("max_retries", 3),
            collection_retry_delay=collection_config.get("retry_delay", 2),
            collection_timeout=collection_config.get("timeout", 60),
            save_incremental=collection_config.get("save_incremental", True),
            logging_level=logging_config.get("level", "INFO")
        )

    def get_fleet_config(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get fleet configuration by name. Returns None if name is None."""
        if name is None:
            return None
        fleet_config = self.fleet_configs.get(name)
        return fleet_config.to_dict() if fleet_config else None

    def get_enabled_handlers(self) -> List[str]:
        """Get list of enabled handler names."""
        return [name for name, handler in self.handlers.items() if handler.enabled]

    def get_handler_fleet_configs(self, handler_name: str) -> List[Optional[str]]:
        """Get list of fleet config names for a specific handler."""
        handler = self.handlers.get(handler_name)
        return handler.fleet_configs if handler else []


def load_config(config_path: Optional[str] = None) -> EvaluationConfig:
    """
    Load evaluation configuration from YAML file.

    Args:
        config_path: Path to config file. If None, uses default.

    Returns:
        EvaluationConfig instance
    """
    if config_path is None:
        # Use default config path
        base_dir = Path(__file__).parent
        config_path = base_dir / "evaluation_config.yaml"

    return EvaluationConfig.from_yaml(str(config_path))


# Constants for fleet configurations (for backward compatibility)
SHIELD_CONFIG = {
    "fleet_portal_version": "v9.20.0",
    "device_apk_version": "v1.20.4",
    "camera_models": ["mitac-gemini", "mitac-sprint-k220"],
    "disabled_standard_events": ["Traffic-Light-Violated"],
    "plan": "SHIELD"
}

NON_SHIELD_CONFIG = {
    "fleet_portal_version": "v10.8.0",
    "device_apk_version": "v1.22.5",
    "camera_models": ["jimi-jc261", "jimi-jc261p"],
    "disabled_standard_events": [],
    "plan": "NON_SHIELD"
}
