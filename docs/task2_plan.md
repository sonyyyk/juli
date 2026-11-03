# Завдання 2 — план: конфігурація, запуск і версії схеми БД

*Стан: **чернетка, чекає на «ок»**. Жодного файлу коду ще не змінено. Єдиний створений файл — цей план.*

## 1. Поточна реалізація (що прочитано)

| Місце | Що є зараз |
|---|---|
| `app/core/database.py` | `DATABASE_PATH = Path("data/juli.db")` — **відносний шлях**, залежить від cwd. `get_connection()` створює каталог і відкриває SQLite. `initialize_database()` створює таблицю `system_snapshots`. |
| Хто викликає `initialize_database()` | **Лише тести.** У застосунку не викликає ніхто, тому на чистій установці Collector падає з `no such table`. |
| `app/core/collector.py` | `collect_system_snapshot()` одразу пише через `get_connection()`. Схему не перевіряє. |
| `app/monitoring/history_reader.py` | `load_recent_snapshots(limit)` через `get_connection()`, `ORDER BY id DESC LIMIT ?`. |
| `app/dashboard.py` | **Свій дубль шляху** `DATABASE_PATH`, свій `sqlite3.connect`, запит **без LIMIT** (усі 1367 рядків реальної бази, далі росте), `ORDER BY timestamp`. «Snapshots collected» рахує `len(data)`. |
| `main.py`, `README.md` | Порожні (0 байт). |
| `app/config.py` | Не існує. Налаштування Ollama живуть у `app/llm/config.py` (не чіпаємо, це Завдання 5). |
| Тести | `test_collector.py` і `test_collector_integration.py` підміняють `database.DATABASE_PATH` і викликають `initialize_database()`. |

**Реальна база `data/juli.db` (прочитано лише в режимі `mode=ro`, нічого не змінено):**
- `PRAGMA user_version = 0`; `journal_mode = delete` (без WAL-файлів);
- одна таблиця `system_snapshots` (+ службова `sqlite_sequence`), індексів немає;
- 1367 рядків, від `2026-10-06T12:20:31Z` до `2026-10-31T14:23:12Z`;
- схема збігається зі схемою міграції 1 дослівно.

## 2. Пропоновані зміни

### 2.1 `app/config.py` (новий)
- `PROJECT_ROOT = Path(__file__).resolve().parent.parent` (не залежить від cwd).
- Значення (змінна оточення → типове):
  - `JULI_DB_PATH` → `PROJECT_ROOT/data/juli.db`;
  - `JULI_COLLECTOR_INTERVAL_SECONDS` → `10`;
  - `JULI_RECENT_WINDOW_MINUTES` → `15`;
  - `JULI_MONITORING_CONTEXT_MAX_CHARS` → `4000`.
- Валідація при читанні: додатні числа, `bool`/`nan`/`inf`/порожній рядок відхиляються, `ValueError` називає змінну. Інтервал і ліміт символів — цілі, вікно recent — додатне скінченне число.
- **Рішення, яке потребує вашого підтвердження — коли читати оточення.** Якщо читати змінні один раз при імпорті, тест із `JULI_DB_PATH` на тимчасовому каталозі не працюватиме після імпорту модулів. Пропоную **читати під час виклику**: функції `get_db_path()`, `get_collector_interval_seconds()`, `get_recent_window_minutes()`, `get_monitoring_context_max_chars()`. Імена `DB_PATH`, `COLLECTOR_INTERVAL_SECONDS` тощо з вашого документа лишаються доступними через модульний `__getattr__` (вони теж обчислюються на вимогу), тож `config.DB_PATH` працює, як описано в завданні.
- Відносний `JULI_DB_PATH` розкривається через `expanduser().resolve()` відносно cwd, а не `PROJECT_ROOT`. Це очікуваніше для змінної оточення, яку задає користувач.

### 2.2 `app/core/database.py`
- `get_connection()` бере шлях із `config.get_db_path()`, створює батьківський каталог. `DATABASE_PATH` у модулі **прибираємо** (єдине джерело — config), а тести переводимо на `JULI_DB_PATH`.
- Список міграцій `MIGRATIONS: list[tuple[int, Callable[[Connection], None]]]`.
  - **Міграція 1:** `CREATE TABLE IF NOT EXISTS system_snapshots (...)` із **тією самою** схемою, що зараз. На наявній базі нічого не змінює в даних і структурі, лише виставляє `user_version = 1`.
- `ensure_schema() -> int` (повертає версію):
  - читає `PRAGMA user_version`; якщо вона вже остання, повертає її, не пишучи нічого;
  - інакше застосовує потрібні міграції по черзі. Кожна міграція виконується в явній транзакції `BEGIN IMMEDIATE`, версія перечитується всередині (на випадок одночасного запуску Collector і dashboard) і виставляється **тією ж транзакцією**, тож при збої відкотиться все, зокрема версія;
  - якщо `user_version` **більша** за відому коду, кидає `RuntimeError` з поясненням, ніколи не «знижує» схему;
  - безпечно викликається повторно.
- `initialize_database()` лишається як сумісний псевдонім до `ensure_schema()`.
- Нових таблиць і змін структури **немає**. Це не зміна схеми в розумінні правила 5 AGENTS.md: змінюється лише службовий номер версії.

### 2.3 `app/core/collector.py`
- `collect_system_snapshot()` викликає `ensure_schema()` **після** успішної перевірки стану й валідації, безпосередньо перед записом. Невдале вимірювання не створює базу, а на порожньому каталозі збір працює без ручної ініціалізації. Помилки БД, як і раніше, не ловляться й зупиняють Collector.
- `run_collector()` типовий інтервал бере з config.

### 2.4 `app/monitoring/history_reader.py`
- Додаю `count_snapshots() -> int` для dashboard. Інший код не змінюється.

### 2.5 `main.py` (argparse, `main(argv=None) -> int`)
- Налаштовує `logging` (рівень INFO, формат `час рівень повідомлення`), щоб попередження Collector із Завдання 1 було видно.
- `init-db` — `ensure_schema()`, друкує шлях до БД, попередню й нову версію.
- `collect [--interval N]` — `ensure_schema()`, потім `run_collector(N)`. Типовий `N` із config; `N` має бути додатним цілим, інакше помилка argparse (код виходу 2).
- `analyze` — `ensure_schema()`, один `run_monitoring_cycle()`, текстове зведення з наявних ключів: `status`, `snapshots_analyzed`, кількість threshold-аномалій, стійких серій та significance assessments, кількість data / temporal / PSI issues. Поля свіжості та `--context` з'являться в Завданнях 3 і 5.
- Без підкоманди друкує довідку й повертає код 2. Помилка (наприклад, `RuntimeError` про нову версію схеми) дає повідомлення в stderr і код 1, а не traceback.
- Порядок запуску відповідає документу: config → `ensure_schema()` → tools → monitoring/agent → UI.

### 2.6 `app/dashboard.py`
- Шлях і підключення — через `app.core.database` (прибираю локальний `DATABASE_PATH` і `sqlite3`).
- `ensure_schema()` при старті скрипта (ідемпотентно).
- Дані: `pd.DataFrame(load_recent_snapshots(limit=2000), columns=[...])`. Це готовий хронологічний запит з `LIMIT`, без нового SQL. Ліміт — константа `DASHBOARD_SNAPSHOT_LIMIT = 2000` у dashboard.
- Підпис «Snapshots collected» показує повну кількість із `count_snapshots()` і дописує «(chart: latest 2000)», щоб не вводити в оману.

## 3. Тести

| Файл | Що перевіряє |
|---|---|
| `tests/test_config.py` (новий) | типові значення; перевизначення через `JULI_*`; помилки для `0`, від'ємних, `abc`, `nan`, `inf`, `true`, порожнього рядка (повідомлення називає змінну); `PROJECT_ROOT` не залежить від cwd (`monkeypatch.chdir`); `config.DB_PATH` відображає змінну, задану після імпорту. |
| `tests/test_database.py` (новий) | чиста БД → таблиця є, `user_version == 1`; два виклики поспіль нічого не ламають; **наявна «стара» база** (`user_version=0`, рядки є) → дані збережені, версія 1, текст схеми в `sqlite_master` не змінився; `user_version` більша за відому → `RuntimeError`, база не змінена; збій міграції відкочується разом із версією (через підміну `MIGRATIONS`); `initialize_database()` працює як псевдонім; батьківський каталог створюється. |
| `tests/test_collector.py`, `test_collector_integration.py` | фікстури переходять з `DATABASE_PATH` на `monkeypatch.setenv("JULI_DB_PATH", ...)`. Новий тест: порожній тимчасовий каталог, `collect_system_snapshot()` без ручної ініціалізації пише рядок. Тест: невдале вимірювання БД не створює. |
| `tests/test_history_reader.py` | `count_snapshots()` на порожній і заповненій базі. |
| `tests/test_main.py` (новий) | `init-db` друкує версію і дає код 0; `collect --interval 3` викликає `run_collector(3)` (мок), типовий інтервал із config, `--interval 0` / `abc` → код 2; `analyze` на тимчасовій базі (PSI підмінений) друкує зведення; без підкоманди → код 2; помилка схеми → код 1. |

`dashboard.py` залежить від Streamlit, тому юніт-тестів не отримує. Його поведінку перевіримо вручну у Завданні 7, а логіку вибірки вже покривають тести `history_reader`.

## 4. Робота з реальною базою `data/juli.db`

Пишуть у неї лише `init-db`, `collect`, `analyze` і dashboard, тобто нічого з автотестів (вони працюють у `tmp_path`). Послідовність:

1. **Що зміниться.** Очікується **лише `PRAGMA user_version`: `0 → 1`**. Таблиця, 1367 рядків, `sqlite_sequence` і текст схеми лишаються без змін. Це я вже підтвердив читанням у режимі `mode=ro`.
2. **Резервна копія** перед будь-яким запуском, що може її змінити: `cp -n data/juli.db data/juli.db.backup-20261103` (`-n`, щоб не затерти наявну копію). Я перевірив, що `.gitignore` (`data/*.db`) **не покриває** такий файл, тому пропоную додати рядок `data/*.db.backup-*`. Без вашого «ок» `.gitignore` не чіпаю.
3. Лише після вашого окремого «ок» запускаю `main.py init-db` на реальній базі й показую: версія 1, `COUNT(*)` той самий (1367), `PRAGMA integrity_check`, схема ідентична резервній копії.

## 5. Що не змінюється
- `significance_evaluator.py`, ключі результату `run_monitoring_cycle()` / `analyze_system_history()`, `app/llm/*`, логіка Collector із Завдання 1.
- Нових залежностей немає. Нових таблиць немає, схема SQLite не змінюється (тільки `user_version`).
- Нічого не комітиться без вашого дозволу.

## 6. Ризики й обмеження
- Повторний `ensure_schema()` на кожен збір (раз на 10 с) — це один дешевий `PRAGMA user_version`. Записів у БД немає, якщо версія вже остання.
- Перехід з `DATABASE_PATH` на `JULI_DB_PATH` ламає будь-який зовнішній код, що підміняв `database.DATABASE_PATH`. У репозиторії такий код є лише в двох тестових файлах, які я оновлю.
- Після цього завдання `analyze` і dashboard на реальній базі теж викличуть `ensure_schema()`, тож перший такий запуск виставить `user_version=1`. Саме тому резервна копія робиться до першого запуску.
- Версії схеми для майбутніх таблиць Memory додаватимуться новими елементами `MIGRATIONS`. У цьому завданні вони не створюються.

## 7. Питання до вас
1. **Читання оточення під час виклику** (функції `get_*()` + ледачі `config.DB_PATH` тощо) замість константи при імпорті — приймаєте?
2. **Відносний `JULI_DB_PATH`** розкривається відносно cwd — підходить?
3. **`data/*.db.backup-*` у `.gitignore`** — додавати?
4. Підпис у dashboard «Snapshots collected: <повна кількість> (chart: latest 2000)» — підходить?
