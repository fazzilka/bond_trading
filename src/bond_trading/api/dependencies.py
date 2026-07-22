from typing import cast

from fastapi import Request

from bond_trading.application.services.imports import ImportPreviewCache
from bond_trading.infrastructure.moex import MoexIssClient


def get_moex_client(request: Request) -> MoexIssClient:
    return cast(MoexIssClient, request.app.state.moex_client)


def get_import_cache(request: Request) -> ImportPreviewCache:
    return cast(ImportPreviewCache, request.app.state.import_cache)
