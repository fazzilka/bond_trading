# Bond Trading

Однопользовательское веб-приложение для учёта отдельных лотов облигаций. Оно считает
плановую доходность до выбранного события и текущую доходность, которую теоретически
можно зафиксировать продажей по лучшему bid MOEX. Одинаковые ISIN не агрегируются:
каждая строка покупки остаётся отдельным лотом.

Приложение показывает стоимость покупки, денежные потоки, прибыль до налога,
упрощённую оценку после налога, годовую доходность, разницу текущего и планового
сценариев и доступный объём на лучшем bid.

## Стек

- Python 3.13.12 и `uv` 0.11.3;
- FastAPI, Pydantic v2, Uvicorn;
- SQLAlchemy 2 async, asyncpg, PostgreSQL 18, Alembic;
- HTTPX и Tenacity для MOEX ISS;
- Jinja2, HTMX и CSS;
- Ruff, mypy, pytest, respx и coverage;
- Docker Compose и Prometheus.

Доменное расчётное ядро использует `Decimal` и `date`, не зависит от FastAPI, БД или
MOEX. Временные метки хранятся в UTC, бизнес-даты интерпретируются в
`Europe/Moscow`.

## Быстрый старт через Docker

Нужны Docker Desktop или Docker Engine с Compose v2.

```bash
cp .env.example .env
docker compose up --build -d
curl http://127.0.0.1:8000/health
```

Миграции применяет одноразовый сервис `migrate`; backend стартует только после его
успешного завершения. Интерфейс доступен на <http://127.0.0.1:8000/portfolio>, OpenAPI
— на <http://127.0.0.1:8000/docs>.

Проверить состояние и остановить стек:

```bash
docker compose ps
docker compose logs migrate backend
docker compose down
```

PostgreSQL не публикуется на хост. Prometheus вынесен в необязательный профиль:

```bash
docker compose --profile monitoring up -d
```

После запуска он доступен только локально на <http://127.0.0.1:9090>.

## Быстрый старт через uv

Нужны `uv` и доступный PostgreSQL. Python устанавливается и зависимости
восстанавливаются строго из lock-файла:

```bash
uv python install 3.13.12
uv sync --frozen
cp config.toml.example config.toml
```

Укажите реальный локальный URL PostgreSQL в `config.toml`, затем выполните:

```bash
uv run alembic upgrade head
uv run uvicorn bond_trading.main:app --reload
```

Значения окружения имеют приоритет над `config.toml`. Вложенные ключи задаются через
двойное подчёркивание, например:

```bash
export BOND_TRADING__DATABASE__URL='postgresql+asyncpg://user:password@127.0.0.1:5432/bond_trading'
export BOND_TRADING__LOGGING__LEVEL='DEBUG'
```

## Миграции

Применить миграции:

```bash
uv run alembic upgrade head
```

Проверить SQL без изменения БД:

```bash
uv run alembic upgrade head --sql
```

В первом срезе одна миграция: `20260722_0001_initial_schema.py`. Она создаёт
инструменты, лоты, корпоративные события, рыночные и расчётные снимки, импорты и
настройки.

## Импорт портфеля

Поддерживается `.xlsx`. Файлы `.numbers` и старые бинарные `.xls` сначала нужно
экспортировать в XLSX. Через страницу <http://127.0.0.1:8000/import> файл проходит
preview и только затем подтверждается.

Для листа `Доход счёт 2026` импортёр:

- находит строку с заголовком `ISIN`;
- читает непрерывный блок со следующей строки до первой полностью пустой строки;
- не импортирует расположенный ниже контрольный блок;
- нормализует ISIN в верхний регистр и проверяет контрольную цифру;
- сохраняет повторные ISIN отдельными лотами;
- возвращает ошибки с номером исходной строки;
- не принимает формулы в ручных полях;
- обеспечивает повторяемость по checksum файла и листу.

Расчётные значения из Excel не считаются источником истины и пересчитываются
приложением. Подробная карта колонок приведена в
[docs/import-format.md](docs/import-format.md).

## API

Операционные endpoints:

- `GET /health`;
- `GET /metrics`.

Инструменты и рынок:

- `GET /api/v1/instruments`;
- `GET /api/v1/instruments/{isin}`;
- `POST /api/v1/instruments/{isin}/refresh`;
- `POST /api/v1/market/refresh-all`.

Лоты и расчёты:

- `GET /api/v1/lots`;
- `POST /api/v1/lots`;
- `GET /api/v1/lots/{lot_id}`;
- `PATCH /api/v1/lots/{lot_id}`;
- `DELETE /api/v1/lots/{lot_id}`;
- `POST /api/v1/lots/{lot_id}/calculate`;
- `GET /api/v1/lots/{lot_id}/yield-history`.

Импорт и настройки:

- `POST /api/v1/imports/preview`;
- `POST /api/v1/imports/commit`;
- `GET /api/v1/imports/{batch_id}`;
- `GET /api/v1/settings`;
- `PATCH /api/v1/settings`.

Ошибки API имеют поля `code`, `message`, `details` и `request_id`. Полные схемы и
примеры доступны в OpenAPI.

## Проверки

Полный локальный набор:

```bash
uv lock --check
uv sync --frozen
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest
```

Покрытие чистого расчётного ядра:

```bash
uv run coverage run -m pytest tests/unit/calculations
uv run coverage report --include='*/domain/calculations/*'
```

Live smoke-тесты MOEX отключены по умолчанию. Запуск с сетью:

```bash
BOND_TRADING_RUN_LIVE_TESTS=1 uv run pytest -m live tests/live
```

## Ограничения и безопасность

- MOEX ISS может возвращать задержанные или неполные публичные данные. Приложение не
  называет их realtime, показывает время получения, время рынка, свежесть и
  `delayed: unknown`, когда режим задержки невозможно определить.
- Отсутствующий `BID` не заменяется последней сделкой. Если глубина лучшего bid меньше
  позиции, показанная доходность является теоретической только для доступного объёма.
- Налоговые режимы `flat_rate` и `legacy_divide_1_13` — упрощённые оценки. Они не
  моделируют индивидуальные льготы, вычеты, сальдирование и правила брокера.
- Preview хранится в памяти процесса 30 минут. Для нескольких реплик потребуется
  внешний cache/storage.
- `/health` проверяет жизнеспособность процесса, а не готовность MOEX или состояние
  всех данных портфеля.
- MVP не содержит авторизации и предназначен только для одного локального
  пользователя. Не выставляйте его в Интернет без reverse proxy, TLS, authentication
  и отдельной проверки эксплуатационной безопасности.

Приложение и его расчёты не являются инвестиционной, бухгалтерской или налоговой
рекомендацией.

## Дополнительная документация

- [Архитектура](docs/architecture.md)
- [Правила расчётов](docs/calculations.md)
- [MOEX ISS и неоднозначные поля](docs/moex-data.md)
- [Формат импорта](docs/import-format.md)
