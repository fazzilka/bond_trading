# MinIO и хранение файлов

## Что хранится где

PostgreSQL хранит пользователя, исходное имя, object key, MIME, формат, размер,
SHA-256, статус обработки и текст ошибки. Сами байты таблицы хранятся в приватном
bucket `bond-trading-uploads` в MinIO.

Object key не строится напрямую из пользовательского пути:

```text
users/{owner_uuid}/uploads/{upload_uuid}/{safe_file_name}
```

Это разделяет пространства пользователей и исключает перезапись файла с тем же
именем. Скачивание всё равно проверяет owner ID; администратор имеет отдельное
расширенное право.

## Жизненный цикл

1. Backend проверяет расширение, MIME и лимит 10 MiB.
2. Оригинал записывается в MinIO.
3. Метаданные фиксируются в PostgreSQL со статусом `uploaded`.
4. Успешный разбор меняет статус на `parsed`, ошибка — на `failed`.
5. Commit меняет статус на `imported`.
6. Повтор уже импортированного содержимого получает статус `duplicate`.

Если запись метаданных в PostgreSQL не удалась, только что созданный S3-объект
удаляется компенсирующей операцией.

## Локальный запуск

Compose публикует S3 API на <http://127.0.0.1:9000> и web console на
<http://127.0.0.1:9001>. Учётные данные задаются `MINIO_ROOT_USER` и
`MINIO_ROOT_PASSWORD` в `.env`. Backend внутри Compose использует endpoint
`minio:9000`.

Community Edition MinIO теперь распространяется как source-only. Поэтому Compose
собирает MinIO из закреплённого security-релиза
`RELEASE.2025-10-15T17-29-55Z`, а не скачивает плавающий или более старый готовый
образ. Первая сборка занимает заметно больше времени; затем Docker использует cache.

Проверить состояние:

```bash
docker compose ps minio
docker compose logs minio
```

Для отдельного внешнего S3-совместимого сервиса задаются:

```text
BOND_TRADING__STORAGE__ENDPOINT
BOND_TRADING__STORAGE__ACCESS_KEY
BOND_TRADING__STORAGE__SECRET_KEY
BOND_TRADING__STORAGE__BUCKET
BOND_TRADING__STORAGE__SECURE
BOND_TRADING__STORAGE__REGION
```

`secure=true` означает HTTPS. Bucket создаётся при старте, если credentials дают такое
право.

## Production

В production следует создать отдельного S3-пользователя с доступом только к нужному
bucket, включить TLS, версионирование/retention по требованиям, lifecycle policy,
мониторинг свободного места и резервное копирование. PostgreSQL и MinIO нужно
восстанавливать согласованно: без метаданных object key файлы теряют связь с
пользователем, а без объектов download и повторная диагностика невозможны.
