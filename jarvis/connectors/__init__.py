"""Connector contracts and implementations for the always-on event runtime."""

from .base import BaseConnector, ConnectorPollResult
from .academics import AcademicsFeedConnector
from .academics_calendar import AcademicCalendarConnector
from .academics_gmail import GmailAcademicsConnector
from .academics_google_calendar import GoogleCalendarConnector
from .academics_materials import AcademicMaterialsConnector
from .ci_reports import JsonCIReportConnector
from .git_native import GitNativeRepoConnector
from .markets_calendar import MarketsCalendarConnector
from .markets_outcomes import MarketsOutcomesConnector
from .markets_positions import MarketsPositionsConnector
from .markets_signals import MarketsSignalsConnector
from .personal_context import PersonalContextConnector
from .repo import RepoChangeConnector

__all__ = [
    "BaseConnector",
    "ConnectorPollResult",
    "AcademicsFeedConnector",
    "AcademicCalendarConnector",
    "GoogleCalendarConnector",
    "GmailAcademicsConnector",
    "AcademicMaterialsConnector",
    "MarketsSignalsConnector",
    "MarketsPositionsConnector",
    "MarketsCalendarConnector",
    "MarketsOutcomesConnector",
    "PersonalContextConnector",
    "RepoChangeConnector",
    "GitNativeRepoConnector",
    "JsonCIReportConnector",
]
