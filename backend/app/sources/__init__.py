from app.sources.ashby import AshbySource
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
    AshbySource.name: AshbySource,
}

__all__ = [
    "JobSource",
    "NormalizedJob",
    "AshbySource",
    "GreenhouseSource",
    "LeverSource",
    "SmartRecruitersSource",
    "WorkdaySource",
    "SOURCES",
]
