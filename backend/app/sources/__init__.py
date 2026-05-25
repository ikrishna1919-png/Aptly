from app.sources.base import JobSource, NormalizedJob
from app.sources.greenhouse import GreenhouseSource
from app.sources.lever import LeverSource

SOURCES: dict[str, type[JobSource]] = {
    GreenhouseSource.name: GreenhouseSource,
    LeverSource.name: LeverSource,
}

__all__ = ["JobSource", "NormalizedJob", "GreenhouseSource", "LeverSource", "SOURCES"]
