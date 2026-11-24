"""Event loop helpers.

psycopg's async API does not support Windows' default ``ProactorEventLoop``: it needs a
``SelectorEventLoop``. Use ``selector_loop_factory`` wherever a loop is created, e.g.
``uvicorn app.main:app --loop app.core.aio:selector_loop_factory``.
"""

import asyncio

UVICORN_LOOP_OPTION = "--loop app.core.aio:selector_loop_factory"


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Return a new ``SelectorEventLoop`` (psycopg async rejects the Windows Proactor loop).

    Takes no argument: uvicorn calls the object imported by ``--loop module:callable`` as is.
    """
    return asyncio.SelectorEventLoop()


def ensure_selector_event_loop() -> None:
    """Refuse to start on a Proactor loop, with the fix, instead of failing at the first query."""
    proactor = getattr(asyncio, "ProactorEventLoop", None)  # exists on Windows only
    if proactor is not None and isinstance(asyncio.get_running_loop(), proactor):
        raise RuntimeError(
            "The running event loop is a ProactorEventLoop, which psycopg's async API cannot use. "
            f"Start the server with: uv run uvicorn app.main:app {UVICORN_LOOP_OPTION} "
            "(tests: TestClient(..., backend_options={'loop_factory': asyncio.SelectorEventLoop}))."
        )
