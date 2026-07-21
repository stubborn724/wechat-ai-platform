"""Agent execution log service — persists execution traces for observability."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.mysql_models import AgentLog


def save_log(
    db: Session,
    log_data: Dict[str, Any],
) -> AgentLog:
    """Persist a single agent execution log entry.

    Expected keys in *log_data*
    --------------------------
    ``task_id`` (str, required)
        The article task UUID.
    ``agent_name`` (str, required)
        Name of the agent (e.g. ``"agent1_title"``).
    ``status`` (str, required)
        Execution status (e.g. ``"success"``, ``"failed"``).
    ``prompt`` (str, optional)
        The prompt sent to the LLM.
    ``input_data`` (dict, optional)
        Structured input passed to the agent.
    ``output_data`` (dict, optional)
        Structured output produced by the agent.
    ``error_message`` (str, optional)
        Error details if the agent failed.
    ``start_time`` (datetime | str, optional)
        When execution started.
    ``end_time`` (datetime | str, optional)
        When execution finished.
    ``duration_ms`` (int, optional)
        Wall-clock duration in milliseconds.
    """
    now = datetime.now(tz=timezone.utc)

    def _parse_dt(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return None

    log_entry = AgentLog(
        task_id=log_data["task_id"],
        agent_name=log_data["agent_name"],
        status=log_data["status"],
        prompt=log_data.get("prompt"),
        input_data=log_data.get("input_data"),
        output_data=log_data.get("output_data"),
        error_message=log_data.get("error_message"),
        start_time=_parse_dt(log_data.get("start_time")) or now,
        end_time=_parse_dt(log_data.get("end_time")),
        duration_ms=log_data.get("duration_ms"),
    )

    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry
