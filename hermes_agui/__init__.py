"""hermes-agui: AG-UI / CopilotKit runtime bridge and frontend tools for Hermes Agent."""

from .api import FRONTEND_TOOL, Broker, HermesClient, TranscriptStore
from .surface import FrontendToolSpec, Principal, Surface, SurfaceRegistry, TurnContext

__version__ = "0.1.1"

__all__ = [
    "FRONTEND_TOOL",
    "Broker",
    "FrontendToolSpec",
    "HermesClient",
    "Principal",
    "Surface",
    "SurfaceRegistry",
    "TranscriptStore",
    "TurnContext",
    "__version__",
]
