import asyncio
import logging
from functools import wraps
from typing import Callable, Any

from app.database import SessionLocal, engine

logger = logging.getLogger("celery.async_runner")


def run_async_db_task(async_fn: Callable[..., Any]) -> Callable[..., Any]:
    """
    Industry-standard wrapper for running Async SQLAlchemy inside Celery.

    1. Executes the coroutine inside a fresh event loop via asyncio.run().
    2. Opens a managed AsyncSession and passes `db` as the first argument.
    3. Guarantees `await engine.dispose()` runs before the loop closes, preventing
       `Future attached to a different loop` and database connection leaks.
    """
    @wraps(async_fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        async def _runner():
            async with SessionLocal() as db:
                try:
                    return await async_fn(db, *args, **kwargs)
                except Exception as e:
                    logger.exception("Task failed in %s: %s", async_fn.__name__, e)
                    raise
                finally:
                    # Crucial: Disposes connections bound to this event loop before it closes
                    await engine.dispose()

        return asyncio.run(_runner())

    return wrapper