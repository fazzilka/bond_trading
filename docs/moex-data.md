# Данные MOEX ISS

Основной источник — публичный [MOEX ISS](https://iss.moex.com/iss/reference/).
`MoexIssClient` изолирует application/domain от формата `columns` + `data`.

## Нужен ли ключ MOEX

Для используемых приложением публичных ISS-запросов отдельный API key не нужен.
Приложение обращается к `https://iss.moex.com/iss` самостоятельно. Это не означает,
что данные являются бесплатным realtime-потоком для любого способа распространения:
режим, задержка и допустимое использование зависят от условий MOEX.

Публичный delayed-режим в `.env`:

```text
BOND_TRADING__MOEX__PASSPORT_LOGIN=
BOND_TRADING__MOEX__PASSPORT_PASSWORD=
BOND_TRADING__MOEX__REQUIRE_AUTH=false
```

Для подписанного MOEX Passport доступа нужно зарегистрировать email в MOEX Passport,
подключить у Биржи нужную информационную услугу и заполнить:

```text
BOND_TRADING__MOEX__PASSPORT_LOGIN=your-email@example.com
BOND_TRADING__MOEX__PASSPORT_PASSWORD=your-private-password
BOND_TRADING__MOEX__REQUIRE_AUTH=true
```

При startup backend выполняет Basic-auth запрос по HTTPS на
`https://passport.moex.com/authenticate`, получает cookie `MicexPassportCert` и
использует её для последующих ISS-запросов. Пароль не логируется и представлен в
конфигурации как secret. Если `BOND_TRADING__MOEX__REQUIRE_AUTH=true`, отсутствие
пары credentials или сертификата не даст backend запуститься.

MOEX Passport не является торговым API: он открывает подписанные информационные
разделы ISS. Для выставления заявок нужны брокерский/торговый интерфейс и отдельные
договоры/идентификаторы MOEX.

Проверить поиск бумаги напрямую:

```bash
curl 'https://iss.moex.com/iss/securities.json?q=RU000A107SX3'
```

В обычной работе вручную разбирать ответ не требуется: вызов
`POST /api/v1/instruments/RU000A107SX3/refresh` находит точный ISIN, определяет SECID
и доску, затем сохраняет спецификацию, котировку и календарь выплат.

## Запросы одного refresh

1. `/securities.json?q={isin}` — поиск точного ISIN и SECID.
2. `/securities/{secid}.json` — описание инструмента.
3. `/engines/stock/markets/bonds/boards/{board}/securities/{secid}.json` —
   спецификация бумаги и marketdata выбранной доски.
4. `/statistics/engines/stock/markets/bonds/bondization/{secid}.json` — coupons,
   amortizations и offers.

Поиск может вернуть несколько строк, поэтому mapper сравнивает нормализованный ISIN
на точное равенство и предпочитает торгуемый результат. Whitelist ISIN отсутствует.
Если активной рыночной строки нет, инструмент всё равно может быть сохранён со
статусом `no_market_data`.

## Решения по полям

| Значение приложения | Поле ISS | Решение |
|---|---|---|
| лучший bid, % | `marketdata.BID` | единственная цена для current сценария |
| bid, руб./бумагу | derived | `BID × FACEVALUE / 100` |
| глубина лучшего bid | `marketdata.BIDDEPTH` | не заменяется агрегированным `BIDDEPTHT` |
| размер лота | `securities.LOTSIZE` | default 1 только при отсутствии поля |
| непогашенный номинал | `securities.FACEVALUE` | fallback на description `FACEVALUE` |
| валюта номинала | `FACEUNIT` | историческое `SUR` нормализуется в `RUB` |
| НКД | `securities.ACCRUEDINT` | `null` остаётся `null`, не превращается в ноль |
| последняя сделка | `marketdata.LAST` | сохраняется только для информации |
| рыночное время | `marketdata.SYSTIME` | локальное московское время переводится в UTC |
| delayed | нет надёжного публичного флага | сохраняется `unknown` |

`BID` никогда не подменяется `LAST`. Нулевое значение, `null` и отсутствующая колонка
различаются: отсутствующее необязательное поле допускается и диагностируется, а
невалидный Decimal/date вызывает data error.

## Corporate actions

- `coupons.coupondate`, `value_rub`/`value`, `valueprc`, `recorddate`;
- `amortizations.amortdate`, `value_rub`/`value`, `valueprc`;
- `offers.offerdate`, `value_rub`/`value`, `valueprc`.

Строка amortization с `data_source=maturity` нормализуется как `maturity`. Hash
нормализованной исходной строки участвует в уникальности и не позволяет повторно
записать то же событие.

## Надёжность

- один HTTPX `AsyncClient` живёт весь lifespan;
- timeout по умолчанию 10 секунд;
- до трёх попыток с exponential backoff для transport errors, HTTP 429 и 5xx;
- semaphore ограничивает параллельные запросы;
- успешный результат кэшируется на 15 минут;
- конкретный User-Agent задаётся конфигурацией;
- исходные блоки `securities`, `marketdata` и `dataversion` сохраняются в JSONB
  market snapshot для диагностики.

Если refresh существующего инструмента падает, последняя сохранённая котировка не
заменяется пустым снимком и её timestamp не обновляется. Снимок получает
`refresh_error`, а UI показывает текст ошибки рядом со статусом свежести.

## Ограничения

Публичный ISS может отдавать задержанные данные, закрытую доску, отсутствующий bid,
пустой НКД или изменённый набор необязательных колонок. UI поэтому показывает источник,
market timestamp, received timestamp, freshness, delayed/unknown и последнюю ошибку.
Эти данные нельзя трактовать как гарантированную исполнимую realtime-котировку.

Обычные unit и integration tests не обращаются к сети. Responses моделируются через
respx/fixtures; отдельные live smoke tests запускаются только при установленной
`RUN_LIVE_MOEX=1`.
