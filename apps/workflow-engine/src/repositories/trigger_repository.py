"""Repository for active trigger management."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from croniter import croniter
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, col

from ..db.models import ActiveTriggerModel

logger = logging.getLogger(__name__)


class TriggerRepository:
    """CRUD + query for active triggers."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sync_triggers(
        self,
        workflow_id: str,
        version_id: int | None,
        definition: dict[str, Any],
        team_id: str = "default",
    ) -> list[ActiveTriggerModel]:
        """Delete old triggers and insert new ones from the workflow definition."""
        await self.deactivate_triggers(workflow_id)

        triggers: list[ActiveTriggerModel] = []
        for node in definition.get("nodes", []):
            node_type = node.get("type", "")
            node_name = node.get("name", "")
            params = node.get("parameters", {})

            if node_type == "Webhook":
                path = params.get("path", "")
                if path:
                    trigger = ActiveTriggerModel(
                        workflow_id=workflow_id,
                        workflow_version_id=version_id,
                        team_id=team_id,
                        node_name=node_name,
                        type="webhook",
                        webhook_path=path,
                        config={
                            "path": path,
                            "method": params.get("method", "POST"),
                            "response_mode": params.get("responseMode", "onReceived"),
                        },
                    )
                    self._session.add(trigger)
                    triggers.append(trigger)

            elif node_type == "Cron":
                expression = params.get("cronExpression", "")
                interval = params.get("interval")
                if expression:
                    try:
                        cron = croniter(expression, datetime.now())
                        next_run = cron.get_next(datetime)
                    except (ValueError, KeyError):
                        logger.warning("Invalid cron expression: %s", expression)
                        continue

                    trigger = ActiveTriggerModel(
                        workflow_id=workflow_id,
                        workflow_version_id=version_id,
                        team_id=team_id,
                        node_name=node_name,
                        type="cron",
                        config={"expression": expression},
                        next_run_at=next_run,
                    )
                    self._session.add(trigger)
                    triggers.append(trigger)
                elif interval:
                    trigger = ActiveTriggerModel(
                        workflow_id=workflow_id,
                        workflow_version_id=version_id,
                        team_id=team_id,
                        node_name=node_name,
                        type="interval",
                        config={"seconds": int(interval)},
                        next_run_at=datetime.now(),
                    )
                    self._session.add(trigger)
                    triggers.append(trigger)

        await self._session.flush()
        return triggers

    async def find_webhook_trigger(
        self, path: str, method: str
    ) -> ActiveTriggerModel | None:
        """Find an enabled webhook trigger by path and method."""
        stmt = select(ActiveTriggerModel).where(
            ActiveTriggerModel.webhook_path == path,
            ActiveTriggerModel.enabled == True,
        )
        result = await self._session.execute(stmt)
        trigger = result.scalars().first()
        if trigger:
            cfg = trigger.config or {}
            if cfg.get("method", "POST") == method:
                return trigger
        return None

    async def get_due_triggers(self) -> list[ActiveTriggerModel]:
        """Get scheduled triggers (cron, interval, polling) that are due to run."""
        now = datetime.now()
        stmt = select(ActiveTriggerModel).where(
            ActiveTriggerModel.type.in_(["cron", "interval", "polling"]),
            ActiveTriggerModel.enabled == True,
            ActiveTriggerModel.next_run_at <= now,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_after_run(self, trigger_id: int) -> None:
        """Update last_run_at, compute next_run_at, and reset error count."""
        trigger = await self._session.get(ActiveTriggerModel, trigger_id)
        if not trigger:
            return

        now = datetime.now()
        trigger.last_run_at = now
        trigger.error_count = 0
        trigger.last_error = None

        if trigger.type == "cron":
            expression = trigger.config.get("expression", "")
            try:
                cron = croniter(expression, now)
                trigger.next_run_at = cron.get_next(datetime)
            except (ValueError, KeyError):
                trigger.enabled = False
                logger.warning("Disabled trigger %d: invalid cron expression", trigger_id)
        elif trigger.type == "interval":
            seconds = trigger.config.get("seconds", 300)
            from datetime import timedelta
            trigger.next_run_at = now + timedelta(seconds=seconds)
        elif trigger.type == "polling":
            seconds = trigger.config.get("interval_seconds", 300)
            from datetime import timedelta
            trigger.next_run_at = now + timedelta(seconds=seconds)

        await self._session.flush()

    async def record_error(self, trigger_id: int, error: str, max_errors: int = 5) -> None:
        """Record a trigger error. Disables trigger after max consecutive failures."""
        trigger = await self._session.get(ActiveTriggerModel, trigger_id)
        if not trigger:
            return

        trigger.error_count += 1
        trigger.last_error = error
        if trigger.error_count >= max_errors:
            trigger.enabled = False
            logger.warning("Disabled trigger %d after %d consecutive errors", trigger_id, max_errors)

        await self._session.flush()

    async def update_state(self, trigger_id: int, state: dict[str, Any]) -> None:
        """Update the runtime state for a trigger (poll cursors, offsets, etc.)."""
        trigger = await self._session.get(ActiveTriggerModel, trigger_id)
        if not trigger:
            return
        trigger.state = state
        await self._session.flush()

    async def deactivate_triggers(self, workflow_id: str) -> None:
        """Delete all triggers for a workflow."""
        stmt = select(ActiveTriggerModel).where(
            ActiveTriggerModel.workflow_id == workflow_id
        )
        result = await self._session.execute(stmt)
        for trigger in result.scalars().all():
            await self._session.delete(trigger)
        await self._session.flush()
