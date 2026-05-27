from app.sources.base import JobSource, NormalizedJob
from app.sources.greenhouse import GreenhouseSource
from app.sources.lever import LeverSource
from app.sources.smartrecruiters import SmartRecruitersSource
from app.sources.workday import WorkdaySource

SOURCES: dict[str, type[JobSource]] = {
    GreenhouseSource.name: GreenhouseSource,
    LeverSource.name: LeverSource,
    SmartRecruitersSource.name: SmartRecruitersSource,
    WorkdaySource.name: WorkdaySource,
}

__all__ = [
    "JobSource",
    "NormalizedJob",
    "GreenhouseSource",
    "LeverSource",
    "SmartRecruitersSource",
    "WorkdaySource",
    "SOURCES",
]
