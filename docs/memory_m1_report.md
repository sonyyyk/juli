# Memory M1 — звіт

*07.11.2026. Крок M1 з `docs/MEMORY_DATA_MODEL_v1.md` (розділ 6). План — `docs/memory_m1_plan.md`. Стан: **реалізовано і погоджено 07.11.2026**; рішення щодо C4 і C15 ухвалено, запобіжник для тестів додано (розд. 5).*

## 1. Коротко

- **Схема.** Міграція `user_version 1 → 2` через наявний `ensure_schema()` створює 9 таблиць з розділу 2. `system_snapshots` не змінюється.
- **Код.** Моделі (dataclasses) і repository в `app/memory/`. Repository створює записи, читає їх і змінює статуси.
- **Інваріанти.** Інваріанти 1–7 перевіряються як запобіжники, і кожна зміна записується в `memory_events`.
- **Резервна копія.** Перед автоміграцією наявної бази **робиться резервна копія**. Якщо копія не вдалася, міграція не виконується.
- **Реальна база.** `data/juli.db` **не змінилась**.
- **Тести.** `pytest -q` → **504 passed, 47 subtests passed** (до M1 було 371). `git diff --check` — без зауважень.
- LLM, AgentCore, CLI (`main.py`), dashboard і моніторинг не змінювались.

## 2. Що зроблено

| Файл | Що |
|---|---|
| `app/core/database.py` | міграція 2 (`MEMORY_SCHEMA_V2`): 9 таблиць, переліки через `CHECK`, `json_valid` для JSON-полів, FK, індекси, `CHECK` для інв. 1, 2 і C15. `get_connection()` вмикає `PRAGMA foreign_keys = ON`. `backup_database()` / `_verify_backup()` — копія перед міграцією |
| `app/memory/models.py` | переліки та 9 frozen dataclasses: `Incident`, `IncidentObservation`, `Hypothesis`, `DiagnosticAction`, `StoredToolResult`, `Evidence`, `MemoryRelation`, `Knowledge`, `MemoryEvent`; валідація полів (переліки, ISO 8601 UTC, скінченні числа, JSON, англійська в `statement`) |
| `app/memory/ids.py` | ID з префіксами `inc_`, `obs_`, `hyp_`, `act_`, `tr_`, `ev_`, `rel_`, `kn_`, `evt_` + uuid4 hex |
| `app/memory/transitions.py` | дозволені переходи статусів і права акторів, чисті функції |
| `app/memory/repository.py` | `MemoryRepository`: `open_incident`, `change_incident_status`, `add_observation`, `propose_hypothesis`, `change_hypothesis_status`, `plan_action`, `change_action_status`, `change_action_approval`, `record_tool_result`, `add_evidence`, `link`, `change_relation_status`, `create_knowledge`, `change_knowledge_status`, `list_recommendable_knowledge`, `list_knowledge_warnings`, `get`, `list_*`. Кожна зміна і її подія — в одній транзакції `BEGIN IMMEDIATE` |
| `app/memory/errors.py` | `NotFound`, `InvalidTransition`, `ActorNotAllowed`, `InvariantViolation` |
| `tests/conftest.py` | фікстури `memory_db`, `repo`, годинник із кроком 1 с, допоміжні сценарії |
| `tests/test_memory_migration.py` | 39 тестів: міграція, резервна копія, FK, `CHECK`, копія реальної БД |
| `tests/test_memory_models.py` | 27 тестів: валідація полів |
| `tests/test_memory_repository.py` | 16 тестів: створення / читання, переходи, посилання, tool results |
| `tests/test_memory_invariants.py` | 43 тести: інваріанти 1–7, C11, C14, C15 |
| `tests/test_real_database_guard.py` | 8 тестів запобіжника реальної БД (розд. 5) |
| `tests/conftest.py` (autouse) | запобіжник: кожен тест — лише з тимчасовою БД; шлях до `data/juli.db` → тест падає |
| `tests/test_database.py`, `test_main.py`, `test_collector.py` | очікувана версія `1` → остання з `MIGRATIONS`; «імітована» наступна міграція — `LATEST + 1`. Зміст тестів не змінено |
| `docs/MEMORY_DATA_MODEL_v1.md` | заголовок «v1.0, погоджено 07.11.2026», D6, розділ 9 (C1–C15), четвертий клас зв'язків |
| `README.md`, `AGENTS.md` | статус: Memory — partial (M1) |

## 3. Рішення C1–C15 і відхилення від специфікації

Позначки: **=** — як у специфікації; **уточнення** — специфікація мовчить або неоднозначна, рішення її доповнює; **відхилення** — реалізація відрізняється від тексту розділів 1–4.

| # | Рішення | Як стосується специфікації |
|---|---|---|
| C1 | `tool_results.error` (JSON, nullable) — поле `error` з `ToolResult` | **відхилення (додано колонку)**: у розд. 2.5 її немає; без неї для `partial` / `error` губиться причина |
| C2 | dataclass `IncidentObservation`, таблиця `observations` | уточнення (D2 — не дублювати `app/models/observation.py`) |
| C3 | інв. 3–5 у M1 — запобіжники; правила, що самі змінюють статус, — M4 | уточнення меж M1 / M4 (розд. 6 відносить інв. 3–5 до M4) |
| C4 | рекомендоване знання — `verified`, `unverified`; попередження — лише `failed`; **`deprecated` retrieval пропускає повністю** | **уточнення інв. 6, погоджено 07.11.2026**: спершу було взято текст специфікації (виключати лише `failed`), потім ти вирішила пропускати й `deprecated`. Зафіксовано в інв. 6 і розд. 9 специфікації |
| C5 | автоматична копія перед міграцією наявної бази; якщо копія не вдалась, міграції немає | уточнення (у специфікації про це нічого) |
| C6 | `foreign_keys = ON` на кожному з'єднанні | уточнення (без цього FK у SQLite не діють) |
| C7 | `actor` ∈ `rule` / `llm` / `user`; `origin = memory` створює `rule`; `juli_suggested` відкриває `user` | уточнення |
| C8 | LLM лише створює записи і переводить гіпотезу `proposed → testing`; `approval` і відкриття інциденту — лише `user`; knowledge — лише `rule` / `user` | уточнення інв. 3 (ширше за «verified / supported / refuted») |
| C9 | підтвердження користувача = подія з `actor = user` і обов'язковим `reason` | уточнення інв. 4 |
| C10 | «нова перевірка» = evidence з tool_result, отриманим дією цього інциденту після створення гіпотези | уточнення інв. 5; підтвердження лише користувачем для `origin = memory` не зараховується |
| C11 | повернення гіпотези з кінцевого статусу в `testing` — лише `rule` з новим доказом або `user` | уточнення (у розд. 2.3 такого переходу немає — це **розширення** життєвого циклу гіпотези) |
| C12 | кирилиця відхиляється **лише в `statement`** | **= специфікації** (8.1). Моя початкова рекомендація перевіряла ще й `evidence.summary`; не взято |
| C13 | payload понад 64 KiB **обрізається** з позначкою `{"truncated": true, "original_size_bytes", "preview"}` | **= специфікації щодо обрізання**. Моя рекомендація була «відхиляти», не взято. **Відхилення:** санітизації в M1 немає (розд. 2.5 її вимагає) — відкладено до M3 разом з відкритим питанням 8.2 |
| C14 | `root_cause_hypothesis_id` — лише `supported` гіпотеза цього інциденту, лише при `resolved` | уточнення |
| C15 | четвертий клас **`evidential`**; `supports` / `contradicts` — завжди `evidential` | **зміна специфікації** (було три класи). Клас додано на твоє прохання; правило відповідності `supports` / `contradicts` → `evidential` запропоноване мною і **погоджене 07.11.2026** |

### Рішення під час реалізації (у специфікації не описані)

1. **Tool results записує лише `rule`** (Tool Layer). Ні LLM, ні користувач не можуть «надати» вивід інструмента, інакше доказ можна підробити.
2. **Дія не запускається без погодження** (`approval` має бути `not_required` або `approved`). `succeeded` вимагає прив'язаного tool_result того самого інструмента. Один tool_result належить лише одній дії. `tool_name` має бути в Tool Registry: `read_check` — read-інструмент, `action` — не-read (у v1 таких немає).
3. **Повторне відкриття інциденту** (подія `reopened`) очищає `closed_at` і `root_cause_hypothesis_id`, а `resolution_summary` лишається. Гіпотеза-причина не може вийти з `supported`, поки інцидент закритий.
4. **Зміна `approval` у журналі** записується як `status_changed` зі значеннями `approval:<старе>` → `approval:<нове>`.
5. **Посилання.** Evidence з `source_type = user` не має `source_ref`. Observation з `snapshot` / `tool_result` мусить мати `source_ref` на запис, що існує. Поліморфні посилання перевіряє repository, бо FK їх не покривають.
6. Журнал пише також `created` (створення записів) і `linked` (зв'язки).
7. **Міграцію 2 змінено на місці**, коли додавався клас `evidential`. Це безпечно: вона не закомічена і не застосована до реальної бази.

## 4. Резервна копія перед автоміграцією

**Так, робиться.** `ensure_schema()` перед оновленням бази, у якій уже є таблиці:

1. копіює її через SQLite backup API у `<db>.backup-YYYYMMDD-v<стара версія>` (наприклад `data/juli.db.backup-20261107-v1`). Якщо файл уже є, бере ім'я з часом (`…-HHMMSS`); наявну копію **ніколи не перезаписує**;
2. перевіряє копію (`user_version` = стара версія, `PRAGMA quick_check` = `ok`);
3. якщо копію не вдалося зробити або перевірити — `RuntimeError` «migration not applied», база лишається на старій версії.

Порожня нова база мігрує без копії. Копія знімається **до** блокування на запис: SQLite backup API зависає, коли джерело — з'єднання з власною відкритою транзакцією запису (відтворено: зависання понад 10 с). Тому після копії версія перечитується вже під блокуванням.

Наслідок: після злиття **перший** запуск `collect`, `analyze`, `chat` чи dashboard мігрує реальну `data/juli.db` до v2, спершу створивши `data/juli.db.backup-YYYYMMDD-v1`. Шаблон `data/*.db.backup-*` уже є в `.gitignore`.

Тести: копія створюється і містить стару версію та дані; наявна копія не перезаписується; невдала або непідтверджена копія → міграції немає; повторний `ensure_schema()` не робить нової копії.

## 5. Реальна `data/juli.db`

**Не змінилась.**

| | До M1 | Після M1 |
|---|---|---|
| sha256 | `93e211d29e6684ba94f6b88b10fe9ea30402e369ccc46a47b503f4f91ec386ea` | той самий |
| `user_version` | 1 | 1 |
| snapshots | 1393 | 1393 |
| час зміни | 03.11.2026 17:25:48 UTC | той самий |
| файли в `data/` | `juli.db`, `juli.db.backup-20261103`, `raw/` | ті самі |

Міграцію перевірено лише на копіях:

- **pytest** (`test_copy_of_the_real_database_migrates_and_the_original_is_untouched`): копія в `tmp_path`, міграція до v2, snapshots ідентичні, створено копію `…-v1`, sha256 оригіналу до і після однаковий.
- **Вручну** (копія в scratchpad): `main.py init-db` → `Schema version: 1 -> 2`; копія `juli.db.backup-20261107-v1` (версія 1, 1393 snapshots); повторний запуск → `2 -> 2` без нової копії; `main.py analyze` працює на v2.

**Інцидент під час розробки.** Тест відкату журналу спершу викликав `monkeypatch.undo()`, який скинув і тимчасовий `JULI_DB_PATH`, тож одне читання пішло на реальну `data/juli.db`. Запису не було: repository відмовив на перевірці версії (`schema version 2, found 1`) до будь-якої зміни. sha256, версію й файли перевірено одразу. Тест виправлено (`monkeypatch.context()`).

**Запобіжник (додано після цього епізоду).** Autouse-фікстура `_temporary_database_only` у `tests/conftest.py`:

- кожному тесту за замовчуванням дає `JULI_DB_PATH` на новий тимчасовий файл; тест може вказати свій тимчасовий шлях;
- підміняє `app.core.database.get_db_path` (через нього проходять усі з'єднання й резервні копії): якщо шлях, після `resolve()`, — це `data/juli.db`, тест **одразу падає** (`pytest.fail`) ще до відкриття бази. `pytest.fail` — `BaseException`, тож `except Exception` у коді застосунку його не проковтне;
- використовує **власний** `pytest.MonkeyPatch`, тому `monkeypatch.undo()` у тесті запобіжник не знімає.

`app.config.get_db_path` не підмінено: `test_config.py` перевіряє його шлях за замовчуванням без відкриття бази. Пряме читання реального файлу (`sqlite3` `mode=ro`) лишається можливим — ним користується тест копії реальної БД.

Тести (`tests/test_real_database_guard.py`, 8): шлях за замовчуванням тимчасовий; без `JULI_DB_PATH` і з трьома написаннями шляху до `data/juli.db` — падіння; `monkeypatch.undo()` не знімає захисту; `except Exception` не ховає падіння; sha256 і час зміни реальної бази не змінюються. Також вручну: тимчасовий тест, що «забув» тимчасову БД, падає з `test tried to use the real database …`.

## 6. Тести

`.venv/bin/python -m pytest -q` → **504 passed, 47 subtests passed** (22 тестові файли). `git diff --check` — без зауважень.

| Що | Де |
|---|---|
| Інв. 1 — verified causal потребує `basis_evidence_id` | `test_1_*` (3) + `CHECK` у схемі |
| Інв. 2 — `similar_to` лише associative і не підтримує | `test_2_*` (4) |
| Інв. 3 — LLM лише пропонує | `test_3_*` (9) |
| Інв. 4 — `supported` потребує tool evidence або підтвердження користувача, без неспростованого `contradicts` | `test_4_*` (7) |
| Інв. 5 — `origin = memory` потребує нової перевірки (C10) | `test_5_*` (4) |
| Інв. 6 — `failed` лише як попередження, `deprecated` пропускається (C4) | `test_6_*` (2) |
| Інв. 7 — кожна зміна в журналі, відкат без журналу | `test_7_*` (3) |
| C11, C14, C15 | `test_c11_*` (2), `test_c14_*` (2), `test_c15_*` (7) + `CHECK` у схемі |
| Запобіжник реальної БД | `test_real_database_guard.py` (8) |
| Міграція, копія, FK, `CHECK`-переліки = переліки в моделях | `test_memory_migration.py` |

## 7. Обмеження і що далі

- Інваріанти 3–5 — **запобіжники**: ніщо в M1 само не змінює статуси (це M4).
- Ніщо в JULI ще не пише в пам'ять: немає CLI інцидентів (M2), персистування реальних запусків інструментів (M3), Incident Chat (M5, D6).
- Санітизація payload і остаточний ліміт — M3.
- Рішення 07.11.2026: C15 лишається; retrieval у M7 пропускає `deprecated` повністю (C4).
