import asyncio

from app.core.aio import selector_loop_factory


def test_selector_loop_factory_returns_selector_event_loop() -> None:
    loop = selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()
