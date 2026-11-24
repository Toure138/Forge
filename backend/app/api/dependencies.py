"""Small FastAPI dependencies shared by the routes of spec 001 (organizations, members,
invitations, audit): the application's clock and settings, from `app.state`."""

from typing import Annotated

from fastapi import Depends, Request

from app.core.clock import Clock
from app.core.config import Settings


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


ClockDep = Annotated[Clock, Depends(_clock)]
SettingsDep = Annotated[Settings, Depends(_settings)]
