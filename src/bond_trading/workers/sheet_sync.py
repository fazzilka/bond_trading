import asyncio
import logging
import signal
import time
from collections.abc import Callable
from contextlib import suppress

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bond_trading.application.services.sheets import (
    SheetSyncService,
    claim_next_sheet_sync_job,
    enqueue_due_sheet_syncs,
    record_sheet_sync_failure,
)
from bond_trading.core.config import AppSettings, get_settings
from bond_trading.core.logging import configure_logging
from bond_trading.core.metrics import (
    SHEET_SYNC_DURATION,
    SHEET_SYNC_JOBS,
    SHEET_SYNC_LAST_SUCCESS,
    SHEET_SYNC_ROWS,
)
from bond_trading.infrastructure.db.session import Database
from bond_trading.infrastructure.google_sheets import (
    GoogleServiceAccountSheetsGateway,
    GoogleSheetsGateway,
)
from bond_trading.infrastructure.moex import MoexIssClient

logger = logging.getLogger(__name__)


async def process_one_job(
    session_factory: async_sessionmaker[AsyncSession],
    gateway: GoogleSheetsGateway,
    moex: MoexIssClient,
    settings: AppSettings,
) -> bool:
    async with session_factory() as session:
        await enqueue_due_sheet_syncs(session)
        job = await claim_next_sheet_sync_job(session)
        if job is None:
            return False
        started = time.perf_counter()
        try:
            completed = await SheetSyncService(session).execute(
                job,
                gateway,
                moex,
                settings.business_timezone,
            )
        except Exception as exc:
            await record_sheet_sync_failure(session, job, exc)
            SHEET_SYNC_JOBS.labels("ошибка", job.trigger.value).inc()
            logger.exception(
                "Ошибка синхронизации Google Таблицы",
                extra={"event": "sheet_sync_failed", "job_id": str(job.id)},
            )
        else:
            SHEET_SYNC_JOBS.labels("успешно", completed.trigger.value).inc()
            SHEET_SYNC_ROWS.inc(completed.rows_updated)
            if completed.completed_at is not None:
                SHEET_SYNC_LAST_SUCCESS.set(completed.completed_at.timestamp())
            logger.info(
                "Google Таблица обновлена",
                extra={
                    "event": "sheet_sync_succeeded",
                    "job_id": str(completed.id),
                    "rows_updated": completed.rows_updated,
                },
            )
        finally:
            SHEET_SYNC_DURATION.observe(time.perf_counter() - started)
        return True


async def run_worker(
    settings: AppSettings,
    *,
    stop_event: asyncio.Event | None = None,
    database_factory: Callable[[AppSettings], Database] = Database,
) -> None:
    configure_logging(settings.logging.level)
    if not settings.google_sheets.enabled:
        logger.warning("Синхронизация Google Таблиц выключена в конфигурации")
    database = database_factory(settings)
    http_client = httpx.AsyncClient(
        base_url=settings.moex.base_url,
        timeout=settings.moex.timeout_seconds,
        headers={"User-Agent": settings.moex.user_agent},
    )
    moex = MoexIssClient(http_client, settings.moex, settings.business_timezone)
    gateway = GoogleServiceAccountSheetsGateway(settings.google_sheets)
    await moex.authenticate()
    stopped = stop_event or asyncio.Event()
    try:
        while not stopped.is_set():
            if settings.google_sheets.enabled:
                processed = await process_one_job(
                    database.session_factory,
                    gateway,
                    moex,
                    settings,
                )
                if processed:
                    continue
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    stopped.wait(), timeout=settings.google_sheets.worker_poll_seconds
                )
    finally:
        await gateway.close()
        await http_client.aclose()
        await database.close()


def main() -> None:
    settings = get_settings()

    async def runner() -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_name, stop_event.set)
        await run_worker(settings, stop_event=stop_event)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
