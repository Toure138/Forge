from datetime import datetime

from app.agents.assistant.tools import get_current_time


def test_get_current_time_is_iso_utc():
    value = get_current_time.invoke({})
    assert datetime.fromisoformat(value).utcoffset().total_seconds() == 0
