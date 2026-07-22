import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from bond_trading.core.config import MoexSettings
from bond_trading.domain.value_objects import normalize_isin
from bond_trading.infrastructure.moex.errors import MoexDataError, MoexTemporaryError
from bond_trading.infrastructure.moex.mapper import find_exact_security, map_refresh_result
from bond_trading.infrastructure.moex.schemas import MoexRefreshResult

logger = logging.getLogger(__name__)


class MoexIssClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        settings: MoexSettings,
        timezone: ZoneInfo,
    ) -> None:
        self._http = http_client
        self._settings = settings
        self._timezone = timezone
        self._semaphore = asyncio.Semaphore(settings.concurrency)
        self._cache: dict[str, tuple[float, MoexRefreshResult]] = {}

    async def refresh(self, isin: str, *, force: bool = True) -> MoexRefreshResult:
        normalized = normalize_isin(isin)
        cached = self._cache.get(normalized)
        if not force and cached and cached[0] > time.monotonic():
            return cached[1]

        search_payload = await self._get(
            "/securities.json",
            params={
                "q": normalized,
                "iss.meta": "on",
                "securities.columns": (
                    "secid,shortname,regnumber,name,isin,is_traded,primary_boardid"
                ),
            },
        )
        search_row = find_exact_security(search_payload, normalized)
        secid = str(search_row.get("secid") or "")
        if not secid:
            raise MoexDataError("MOEX search result has no SECID")
        board_id = str(search_row.get("primary_boardid") or "TQCB")
        specification, market, bondization = await asyncio.gather(
            self._get(f"/securities/{secid}.json", params={"iss.meta": "on"}),
            self._get(
                f"/engines/stock/markets/bonds/boards/{board_id}/securities/{secid}.json",
                params={"iss.meta": "on"},
            ),
            self._get(
                f"/statistics/engines/stock/markets/bonds/bondization/{secid}.json",
                params={"iss.meta": "on", "limit": "unlimited"},
            ),
        )
        result = map_refresh_result(
            isin=normalized,
            search_payload=search_payload,
            specification_payload=specification,
            market_payload=market,
            bondization_payload=bondization,
            timezone=self._timezone,
            received_at=datetime.now(UTC),
        )
        self._cache[normalized] = (
            time.monotonic() + self._settings.market_ttl_seconds,
            result,
        )
        logger.debug(
            "MOEX instrument refreshed",
            extra={
                "event": "moex_refresh",
                "source": "MOEX ISS",
                "isin": normalized,
                "reason": {
                    "market_status": result.market.status,
                    "action_count": len(result.actions),
                },
            },
        )
        return result

    async def _get(self, path: str, *, params: Mapping[str, str]) -> dict[str, Any]:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._settings.retries),
            wait=wait_exponential(multiplier=0.2, min=0.2, max=2),
            retry=retry_if_exception_type((httpx.TransportError, MoexTemporaryError)),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                async with self._semaphore:
                    response = await self._http.get(path, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    raise MoexTemporaryError(f"MOEX temporary HTTP {response.status_code}")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise MoexDataError("MOEX response is not a JSON object")
                return payload
        raise AssertionError("Retry loop returned no result")
