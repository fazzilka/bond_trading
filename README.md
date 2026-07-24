# Bond Trading

Многопользовательское веб-приложение для учёта отдельных лотов облигаций. Оно считает
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
- MinIO/S3 для хранения оригиналов загруженных таблиц;
- Argon2, непрозрачные серверные сессии и CSRF-защита;
- openpyxl, python-calamine и numbers-parser для таблиц Excel и Apple Numbers;
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
# Обязательно замените стартовые пароли в .env.
docker compose up --build -d
curl http://127.0.0.1:8000/health
```

Миграции применяет одноразовый сервис `migrate`; backend стартует только после его
успешного завершения, а MinIO — после проверки готовности bucket API. Интерфейс
доступен на <http://127.0.0.1:8000/login>, OpenAPI — на
<http://127.0.0.1:8000/docs>, локальная консоль MinIO — на
<http://127.0.0.1:9001>.

Проверить состояние и остановить стек:

```bash
docker compose ps
docker compose logs migrate backend
docker compose down
```

PostgreSQL не публикуется на хост. Порты backend, MinIO и Prometheus привязаны только
к `127.0.0.1`. Prometheus вынесен в необязательный профиль:

```bash
docker compose --profile monitoring up -d
```

После запуска он доступен только локально на <http://127.0.0.1:9090>.

## Быстрый старт через uv

Нужны `uv`, доступный PostgreSQL и S3-совместимое хранилище. Для локальной разработки
проще запустить инфраструктуру через Compose:

```bash
uv python install 3.13.12
uv sync --frozen
cp config.toml.example config.toml
docker compose up -d postgres minio
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
export BOND_TRADING__STORAGE__ENDPOINT='127.0.0.1:9000'
export BOND_TRADING__LOGGING__LEVEL='DEBUG'
```

## Авторизация

При первом старте автоматически создаются три активных аккаунта: `admin`, `user1` и
`user2`. Их пароли берутся из `.env`/environment:

- `BOOTSTRAP_ADMIN_PASSWORD`;
- `BOOTSTRAP_USER1_PASSWORD`;
- `BOOTSTRAP_USER2_PASSWORD`.

Значения из `.env.example` предназначены только для локального запуска. Все стартовые
пользователи получают признак обязательной смены пароля. Сменить пароль можно на
странице `/account`; после этого все сессии пользователя отзываются и нужно войти
заново.

Портфели, импорты, настройки и файлы разделены по владельцам. Обычный пользователь не
видит данные другого пользователя. Администратор на `/admin` видит пользователей и
историю загрузок, создаёт пользователей, назначает роль и блокирует/разблокирует
аккаунты. Система не позволяет отключить текущего или последнего активного
администратора.

Подробности о cookie, Bearer-токене и CSRF приведены в
[docs/authentication.md](docs/authentication.md).

## Миграции

Применить миграции:

```bash
uv run alembic upgrade head
```

Проверить SQL без изменения БД:

```bash
uv run alembic upgrade head --sql
```

`20260722_0001_initial_schema.py` создаёт исходную предметную схему.
`20260725_0002_users_storage.py` добавляет пользователей, серверные сессии, владельцев
данных и метаданные S3-файлов. При обновлении существующие однопользовательские данные
не удаляются: они переходят отключённому служебному владельцу `legacy-owner`.

## Импорт портфеля

Поддерживаются `.numbers`, `.xlsx`, `.xls`, `.xlsm`, `.xlsb`, `.xltx` и `.xltm`.
Через страницу <http://127.0.0.1:8000/import> файл сначала сохраняется в приватный
bucket MinIO, затем проходит preview и только после подтверждения создаёт лоты.
Оригинал, checksum, владелец, размер, формат и статус обработки остаются в журнале
загрузок `/uploads`.

Для листа `Доход счёт 2026` импортёр:

- находит строку с заголовком `ISIN`;
- читает непрерывный блок со следующей строки до первой полностью пустой строки;
- не импортирует расположенный ниже контрольный блок;
- нормализует ISIN в верхний регистр и проверяет контрольную цифру;
- сохраняет повторные ISIN отдельными лотами;
- возвращает ошибки с номером исходной строки;
- не принимает формулы в ручных полях;
- обеспечивает повторяемость по владельцу, checksum файла и листу.

Расчётные значения из Excel не считаются источником истины и пересчитываются
приложением. Подробная карта колонок приведена в
[docs/import-format.md](docs/import-format.md), устройство хранилища — в
[docs/storage.md](docs/storage.md).

## API

Операционные endpoints:

- `GET /health`;
- `GET /metrics`.

Авторизация и файлы:

- `POST /api/v1/auth/login`;
- `GET /api/v1/auth/me`;
- `POST /api/v1/auth/logout`;
- `POST /api/v1/auth/change-password`;
- `GET /api/v1/uploads`;
- `GET /api/v1/uploads/{upload_id}/download`;
- `GET /api/v1/admin/users`;
- `POST /api/v1/admin/users`;
- `PATCH /api/v1/admin/users/{user_id}`;
- `GET /api/v1/admin/uploads`.

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
примеры доступны в OpenAPI. Все `/api/v1/*`, кроме `/api/v1/auth/login`, требуют
сессию. Для программного клиента удобнее передать полученный при login токен как
`Authorization: Bearer <token>`.

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
RUN_LIVE_MOEX=1 uv run pytest -m live tests/live
```

## Ограничения и безопасность

- MOEX ISS может возвращать задержанные или неполные публичные данные. Приложение не
  называет их realtime, показывает время получения, время рынка, свежесть и
  `delayed: unknown`, когда режим задержки невозможно определить.
- Отсутствующий `BID` не заменяется последней сделкой. Если глубина лучшего bid меньше
  позиции, показанная доходность является теоретической только для доступного объёма.
- Налоговые режимы `flat_rate` и `legacy_divide_1_13` — упрощённые оценки. Они не
  моделируют индивидуальные льготы, вычеты, сальдирование и правила брокера.
- Оригиналы файлов хранятся в MinIO, но preview пока хранится в памяти процесса
  30 минут. Для нескольких backend-реплик потребуется Redis или другой общий cache.
- `/health` проверяет жизнеспособность процесса, а не готовность MOEX или состояние
  всех данных портфеля.
- Перед публикацией в Интернет обязательны уникальные пароли, TLS/reverse proxy,
  `secure_cookies=true`, резервное копирование PostgreSQL и MinIO, rate limiting
  login/API и отдельная эксплуатационная проверка безопасности.

Приложение и его расчёты не являются инвестиционной, бухгалтерской или налоговой
рекомендацией.

## Дополнительная документация

- [Архитектура](docs/architecture.md)
- [Правила расчётов](docs/calculations.md)
- [MOEX ISS и неоднозначные поля](docs/moex-data.md)
- [Формат импорта](docs/import-format.md)
- [Авторизация и роли](docs/authentication.md)
- [MinIO и хранение файлов](docs/storage.md)
- [Процесс веток и задач](docs/development.md)
