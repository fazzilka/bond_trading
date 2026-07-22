# Архитектура

## Границы приложения

Код организован по направлению зависимостей:

```text
presentation / api
        ↓
application services
        ↓
domain calculations and value objects

infrastructure adapters → application services
```

`domain` ничего не знает о FastAPI, SQLAlchemy, файлах и HTTP. `application`
координирует use cases и транзакции. `infrastructure` адаптирует PostgreSQL, MOEX ISS
и XLSX. `api` и `presentation` являются двумя входами в одни сервисы.

## Ключевые каталоги

```text
src/bond_trading/
  api/                    JSON API, schemas, dependencies
  application/services/   import, instruments, lots, settings
  core/                   config, logs, metrics, request middleware
  domain/
    calculations/         чистые Decimal/date формулы
    value_objects/        нормализация и проверка ISIN
  infrastructure/
    db/                   async SQLAlchemy models/session
    imports/              XLSX reader
    moex/                 ISS client, mapper, DTO
  presentation/           Jinja2 templates, HTMX, CSS
  main.py                 composition root и lifespan
alembic/                  миграции PostgreSQL
tests/                    unit, integration, opt-in live
```

## Runtime

Composition root создаёт один `Database`, один переиспользуемый HTTPX `AsyncClient`,
`MoexIssClient` и in-memory cache preview на lifespan FastAPI. Сессия SQLAlchemy
создаётся на запрос и откатывается при исключении. Простые FastAPI dependencies
оставлены вместо отдельного DI-фреймворка: текущий граф зависимостей мал, а тестовые
override остаются прозрачными.

Request middleware создаёт или принимает `X-Request-ID`, измеряет duration и добавляет
идентификатор в JSON-логи. Отдельный middleware публикует счётчик запросов и histogram
latency для Prometheus.

## Данные

- `BondInstrumentModel` — уникальный справочник по uppercase ISIN.
- `BondLotModel` — отдельная покупка; одинаковые ISIN не объединяются.
- `CorporateActionModel` — coupon, amortization, offer и maturity с hash исходной
  строки для дедупликации.
- `MarketSnapshotModel` — bid, глубина, НКД, номинал, timestamps, статус и сырой
  диагностический payload.
- `YieldSnapshotModel` — неизменяемый результат конкретного расчёта и версия формулы.
- `ImportBatchModel` — audit импорта по checksum и листу.
- `AppSettingModel` — одна строка настроек расчёта.

Ручная цена целевого погашения хранится в лоте отдельно от `current_face_value` MOEX.
Для неё обязательна причина и автоматически записывается время изменения. Обновление
override не уничтожает внешний номинал.

## Потоки операций

### Обновление MOEX

1. API нормализует ISIN через application service.
2. Адаптер получает search, specification, marketdata и bondization.
3. Mapper формирует независимые DTO и переводит timestamps в UTC.
4. Service upsert-ит инструмент и новые corporate actions.
5. Новый market snapshot сохраняется целиком.
6. Если обновление существующего инструмента не удалось, предыдущий snapshot
   сохраняется, но получает `refresh_error` и текст последней ошибки без изменения
   времени исходной котировки.

### Расчёт лота

1. Service загружает лот, corporate actions, настройки и последний market snapshot.
2. Формирует только типизированные `Decimal`/`date` входы доменного ядра.
3. Ядро независимо считает planned и, при наличии bid и НКД, current сценарии.
4. Результат и подробная breakdown сохраняются в `YieldSnapshotModel`.

### Импорт

Preview парсится без записи в БД и временно хранится в памяти. Commit повторно берёт
тот же нормализованный preview, в одной транзакции создаёт batch, недостающие
инструменты и отдельный lot на каждую корректную строку. Ошибочные строки остаются в
отчёте batch и не отменяют корректные.

## Конфигурация

Настройки читаются из environment и необязательного `config.toml`. Environment имеет
приоритет. Префикс — `BOND_TRADING__`, разделитель вложенности — `__`.
`config.toml.example` содержит все доступные секции без рабочего секрета.

## Развёртывание

Docker image — multi-stage, зависимости устанавливаются из `uv.lock`, runtime идёт от
непривилегированного пользователя. Compose содержит `postgres`, одноразовый `migrate`
и `backend`; Prometheus подключается только профилем `monitoring`. PostgreSQL не имеет
опубликованного host-порта.

Это локальный single-user runtime. Авторизация, TLS termination, резервное копирование,
rate limiting внешних клиентов и multi-instance preview storage остаются задачами
production-развёртывания.
