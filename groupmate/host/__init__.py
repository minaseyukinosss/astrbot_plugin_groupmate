"""宿主适配层。"""

from .bridge import AstrBotBridge, TurnOwner
from .config import (
    AstrBotConfigParser,
    ConfigDiagnostics,
    ConfigurationError,
    DeploymentSettings,
)
from .llm import (
    AstrBotGenerationModel,
    AstrBotPlatformPort,
    AstrBotVisionPort,
)
from .onebot import NapCatHistoryPort, OneBotTranslator

__all__ = [
    "AstrBotBridge",
    "TurnOwner",
    "AstrBotConfigParser",
    "ConfigDiagnostics",
    "ConfigurationError",
    "DeploymentSettings",
    "AstrBotGenerationModel",
    "AstrBotPlatformPort",
    "AstrBotVisionPort",
    "NapCatHistoryPort",
    "OneBotTranslator",
]
