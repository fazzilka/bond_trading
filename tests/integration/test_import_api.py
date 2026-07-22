import io
from datetime import datetime

import httpx
import openpyxl
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def xlsx_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Доход счёт 2026"
    sheet.append([None])
    sheet.append([None, "ISIN"])
    for isin, quantity in (("RU000A107SX3", 40), ("RU000A107sg8", 8)):
        row: list[object] = [None] * 23
        row[1] = isin
        row[2] = "Bond"
        row[3] = "погашение"
        row[4] = datetime(2026, 5, 25)
        row[6] = datetime(2027, 2, 15)
        row[7] = quantity
        row[8] = 962.9
        row[9] = 3.51
        row[11] = 0.39
        row[13] = 1000
        sheet.append(row)
    sheet.append([None] * 23)
    control = [None] * 23
    control[1] = "RU000A107SX3"
    sheet.append(control)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


async def test_preview_commit_and_idempotency(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], object],
) -> None:
    client, _, _ = app_client
    content = xlsx_bytes()
    preview = await client.post(
        "/api/v1/imports/preview",
        files={
            "file": (
                "portfolio.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert preview.status_code == 200, preview.text
    preview_data = preview.json()
    assert preview_data["rows_read"] == 2
    assert [row["normalized_isin"] for row in preview_data["rows"]] == [
        "RU000A107SX3",
        "RU000A107SG8",
    ]

    committed = await client.post(
        "/api/v1/imports/commit", json={"preview_id": preview_data["preview_id"]}
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["lots_created"] == 2
    assert committed.json()["idempotent_replay"] is False

    second_preview = await client.post(
        "/api/v1/imports/preview",
        files={"file": ("portfolio.xlsx", content)},
    )
    replay = await client.post(
        "/api/v1/imports/commit", json={"preview_id": second_preview.json()["preview_id"]}
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True

    lots = await client.get("/api/v1/lots")
    assert len(lots.json()) == 2
    assert lots.json()[0]["target_redemption_override_reason"] == "Imported from source column N"
    assert lots.json()[0]["target_redemption_override_updated_at"] is not None


async def test_preview_rejects_wrong_extension_and_mime_type(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], object],
) -> None:
    client, _, _ = app_client

    wrong_extension = await client.post(
        "/api/v1/imports/preview",
        files={"file": ("portfolio.xls", xlsx_bytes(), "application/octet-stream")},
    )
    assert wrong_extension.status_code == 422
    assert wrong_extension.json()["code"] == "http_error"

    wrong_mime = await client.post(
        "/api/v1/imports/preview",
        files={"file": ("portfolio.xlsx", xlsx_bytes(), "text/plain")},
    )
    assert wrong_mime.status_code == 422
    assert "MIME" in wrong_mime.json()["message"]


async def test_preview_does_not_trust_valid_upload_metadata(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], object],
) -> None:
    client, _, _ = app_client
    response = await client.post(
        "/api/v1/imports/preview",
        files={
            "file": (
                "portfolio.xlsx",
                b"not a workbook",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 422
    assert "readable XLSX" in response.json()["message"]
