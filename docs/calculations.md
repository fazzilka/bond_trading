# Правила расчётов

Расчётное ядро находится в `src/bond_trading/domain/calculations`. Оно принимает только
dataclass DTO с `Decimal` и `date`. Округление выполняется на границе представления:
внутренние значения не квантуются после промежуточных операций.

## Стоимость покупки

Для количества `q`:

```text
clean_total      = clean_price_per_bond × q
accrued_total    = accrued_interest_per_bond × q
commission_total = commission_per_bond × q
purchase_total   = clean_total + accrued_total + commission_total
```

Комиссия в исходном листе задана на одну облигацию и всегда умножается на количество.

## Границы денежных потоков

Купон или амортизация включается, когда:

```text
purchase_date < event_date <= exit_date
```

Событие в дату покупки не включается; событие в дату выхода включается. Это правило
одинаково для planned и current сценариев. Внутри одного вызова одинаковые события с
тем же типом, датой и суммой учитываются один раз.

В плановом сценарии календарь corporate actions даёт только купоны и амортизации.
Погашение или оферта добавляется отдельным финальным потоком, поэтому maturity не
учитывается дважды.

## Плановый сценарий

```text
coupons_total       = Σ coupon_per_bond × q
amortizations_total = Σ amortization_per_bond × q
redemption_total    = final_redemption_per_bond × q
sale_commission     = sale_commission_per_bond × q

planned_exit_total =
    coupons_total
    + amortizations_total
    + redemption_total
    - sale_commission

planned_profit_before_tax = planned_exit_total - purchase_total
```

Приоритет финальной цены: ручной override лота; совпадающее событие MOEX; для maturity
— текущий непогашенный номинал. Если значение определить нельзя, расчёт возвращает
доменную ошибку.

## Текущий сценарий

Текущая доходность рассчитывается только при наличии лучшего bid и текущего НКД.
Последняя сделка не используется вместо bid.

```text
market_total       = bid_rub_per_bond × q
current_accrued    = current_accrued_per_bond × q
paid_coupons       = Σ coupon_per_bond × q
paid_amortizations = Σ amortization_per_bond × q
sale_commission    = sale_commission_per_bond × q

current_exit_total =
    market_total
    + current_accrued
    + paid_coupons
    + paid_amortizations
    - sale_commission

current_profit_before_tax = current_exit_total - purchase_total
```

Дата оценки по умолчанию берётся из market timestamp и переводится в
`Europe/Moscow`; если timestamp отсутствует, используется московская календарная дата
в момент расчёта.

## Налоговые стратегии

- `none`: прибыль после налога равна прибыли до налога;
- `flat_rate`: к положительной прибыли применяется `profit × (1 - rate)`;
- `legacy_divide_1_13`: положительная прибыль делится на `1.13` для сверки со старой
  таблицей.

Отрицательная и нулевая прибыль ни в одном упрощённом режиме не увеличивается.
Результат после налога — оценочный: приложение не моделирует льготы, вычеты,
сальдирование, брокерские правила и индивидуальный налоговый статус.

## Годовая доходность

```text
holding_days = (exit_date - purchase_date).days

annual_yield_percent =
    profit / purchase_total
    × 365 / holding_days
    × 100
```

Нулевая или отрицательная длительность и неположительная стоимость покупки являются
доменными ошибками. Delta хранится в процентных пунктах:

```text
yield_delta_pp = current_annual_yield - planned_annual_yield
```

## Перевод bid

Для котировки MOEX в процентах от текущего непогашенного номинала:

```text
bid_rub_per_bond = bid_percent × current_face_value / 100
```

Функция также поддерживает уже рублёвую basis, но MOEX mapper использует
`percent_of_face`.

## Текущий расчёт Google Таблицы по OFFER

По согласованному правилу Google Таблица использует не последнюю сделку и не BID, а
лучшую цену предложения MOEX `marketdata.OFFER`:

```text
offer_rub_per_bond = offer_percent × current_face_value / 100

purchase_total =
    purchase_price_per_bond × quantity
    + purchase_accrued_per_bond × quantity
    + commission_from_column_L

current_exit_total =
    offer_rub_per_bond × quantity
    + current_accrued_per_bond × quantity
    + paid_coupons_total
    + paid_amortizations_total
```

Колонка L содержит общую комиссию строки и поэтому прибавляется один раз. Если она
пуста, синхронизация фиксирует `0.4`. Для совместимости с расчётом заказчика текущий
положительный доход после налога делится на `1.13`; отрицательный результат не
увеличивается. Биржевая цена в AA остаётся чистой ценой до налога и без НКД.

## Ликвидность

```text
available_bonds_at_best_bid = bid_depth_lots × lot_size
```

- `sufficient` — объём покрывает весь лот;
- `partial` — покрывает только часть;
- `none` — bid отсутствует или глубина равна нулю;
- `unknown` — bid есть, но объём отсутствует.

При `partial` доходность по лучшей цене относится только к показанному доступному
объёму и не гарантирует закрытие всей позиции.

## Контрольный пример RU000A107SX3

Покупка 40 бумаг 2026-05-25:

```text
962.90 × 40 + 3.51 × 40 + 0.39 × 40 = 38 672.00
```

Старый ошибочный вариант добавлял комиссию `0.39` один раз и давал `38 656.79`.
Regression test явно фиксирует это отличие.

Для погашения 2027-02-15 по 1000, трёх купонов по 39.89 и режима
`legacy_divide_1_13` плановая годовая доходность после упрощённого налога составляет
примерно `19.2008%`.

Для оценки 2026-09-01 с bid 982, одним выплаченным купоном 39.89 и НКД 3.20:

```text
current_exit_total         = 41 003.60
current_profit_before_tax  =  2 331.60
annual_yield_after_tax     ≈    19.6715%
```
