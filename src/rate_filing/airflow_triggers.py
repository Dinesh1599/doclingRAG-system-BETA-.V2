"""Custom Airflow event trigger: fire when a file matching a glob APPEARS.

Airflow 3's event-driven scheduling (AssetWatcher) requires a trigger that
subclasses BaseEventTrigger. The standard provider only ships file *delete* event
triggers, so this provides file *arrival*: the triggerer polls the path
asynchronously and emits a TriggerEvent each time files transition from
absent -> present (one event per arrival "episode"; it re-arms once the watch
path empties again — which our DAG causes by moving processed files out of it).

Lives in the installed `rate_filing` package so the triggerer can import it from
the serialized classpath regardless of process/CWD.
"""

import asyncio
import glob as _glob
from collections.abc import AsyncIterator
from typing import Any

from airflow.triggers.base import BaseEventTrigger, TriggerEvent


class FileArrivalTrigger(BaseEventTrigger):
    def __init__(self, filepath: str, poll_interval: float = 10.0) -> None:
        super().__init__()
        self.filepath = filepath
        self.poll_interval = poll_interval

    def serialize(self) -> tuple[str, dict[str, Any]]:
        return (
            "rate_filing.airflow_triggers.FileArrivalTrigger",
            {"filepath": self.filepath, "poll_interval": self.poll_interval},
        )

    async def run(self) -> AsyncIterator[TriggerEvent]:
        present = False
        while True:
            matches = _glob.glob(self.filepath)
            if matches and not present:
                present = True
                yield TriggerEvent({"matches": sorted(matches)})
            elif not matches:
                present = False     # path emptied -> re-arm for the next arrival
            await asyncio.sleep(self.poll_interval)
