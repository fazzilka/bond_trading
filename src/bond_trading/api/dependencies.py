from typing import cast

from fastapi import Request

from bond_trading.application.services.imports import ImportPreviewCache
from bond_trading.infrastructure.google_sheets import GoogleSheetsGateway
from bond_trading.infrastructure.moex import MoexIssClient
from bond_trading.infrastructure.storage import ObjectStorage


def get_moex_client(request: Request) -> MoexIssClient:
    return cast(MoexIssClient, request.app.state.moex_client)


def get_import_cache(request: Request) -> ImportPreviewCache:
    return cast(ImportPreviewCache, request.app.state.import_cache)


def get_object_storage(request: Request) -> ObjectStorage:
    return cast(ObjectStorage, request.app.state.object_storage)


def get_google_sheets_gateway(request: Request) -> GoogleSheetsGateway:
    return cast(GoogleSheetsGateway, request.app.state.google_sheets_gateway)
