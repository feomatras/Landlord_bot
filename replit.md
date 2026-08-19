# Коммунальный Telegram-бот

Telegram-бот для сбора показаний счётчиков, расчёта коммунальных платежей и контроля оплат по квартирам.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string
- `python main.py` — запуск Telegram-бота
- Workflow `Telegram коммунальный бот` — постоянный фоновый процесс бота
- Required secrets: `TELEGRAM_BOT_TOKEN`, `INITIAL_ADMIN_CODE`

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `main.py` — точка запуска Telegram-бота
- `bot_app/app.py` — команды, диалоги, кнопки и планировщик
- `bot_app/db.py` — SQLite-схема и операции хранения
- `bot_app/calculations.py` — бизнес-правила расчёта
- `bot_app/keyboards.py` — inline-кнопки Telegram
- `data/communal_bot.sqlite3` — локальная база данных (не коммитится)

## Architecture decisions

- Telegram-бот работает через polling и автоматически переживает временные сетевые сбои благодаря повторным попыткам.
- Расчёт электричества следует формуле из раздела 4 ТЗ; она является источником истины при расхождении с примером сообщения.
- Тарифы версионируются по месяцу начала действия, а пересчёт существующих отчётов сохраняет отметки оплаты.
- Для нового арендатора удаление пользователя очищает финансовую историю квартиры по согласованному правилу.

## Product

Администратор создаёт квартиры, приглашает пользователей, настраивает тарифы и начальные показания, вносит УК и отмечает оплаты. Арендаторы получают напоминания, последовательно передают показания и видят прозрачную разбивку итоговой суммы.

## User preferences

- Интерфейс и уведомления на русском языке.
- Месяцы отображаются названием и годом, например «Август 2026».

## Gotchas

- Перед первым `/start` нужно создать хотя бы одну квартиру и настроить начальные показания через меню администратора.
- В текущем ТЗ пример с 80 кВт·ч и суммой 464 руб. противоречит формуле ступенчатого тарифа; код использует формулу, которая даёт 392 руб. при тарифе 4.90.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
