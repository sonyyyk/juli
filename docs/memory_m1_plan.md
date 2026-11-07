# Memory M1 — план: схема v2, моделі, repository, інваріанти

*Стан: **погоджено 07.11.2026**, реалізація M1 почата. Остаточні рішення C1–C14 — у розділі 9 специфікації; там, де вони відрізняються від рекомендацій нижче, діє специфікація.*

Специфікація: `docs/MEMORY_DATA_MODEL_v1.md`, скопійована з `~/Downloads/MEMORY_DATA_MODEL_v1.md` без змін (`cmp` — ідентичні; sha256 `be74a951…dc12a4`). Розділи 0–8 на місці. Заголовок файлу досі каже «чернетка для погодження» / «запропоновано, не реалізовано». Я його не правила, бо копія мала бути без змін.

Обсяг M1 (розділ 6 + твоє уточнення): міграція `user_version 1 → 2` через `ensure_schema()` (9 таблиць з розділу 2); dataclasses у `app/memory/`; repository (створення, читання, зміна статусу); інваріанти розділу 3; запис кожної зміни статусу в `memory_events`. LLM, AgentCore, CLI, dashboard — **не чіпаю**.

## 1. Що є зараз (перевірено)

- `app/core/database.py`: `MIGRATIONS = [(1, …)]`. `ensure_schema()` виконує всі відсутні міграції **в одній транзакції** (`BEGIN IMMEDIATE`) разом з `PRAGMA user_version`. Міграція може використовувати лише `connection.execute` (`executescript` неявно робить COMMIT). Версія новіша за підтримувану → `RuntimeError`, база не змінюється.
- `get_connection()` — звичайний `sqlite3.connect`, **`PRAGMA foreign_keys` = 0** (за замовчуванням у SQLite).
- SQLite 3.45.1 — `json_valid()` і `CHECK` доступні.
- Реальна `data/juli.db` (лише читання, `mode=ro`): `user_version = 1`, таблиці `system_snapshots` (1393 рядки, id INTEGER AUTOINCREMENT) і `sqlite_sequence`. Резервна копія `data/juli.db.backup-20261103` уже є (з Завдання 2).
- `ensure_schema()` викликають: `collect_system_snapshot()` (на кожному snapshot), `main.py` (`init-db`, `collect`, `analyze`, `chat`), `app/dashboard.py`.
- `app/models/observation.py` — `Observation(timestamp, source, metric, value, unit, context)`, frozen dataclass для моніторингу.
- `app/tools/result.py` — `ToolResult = {tool, status, data, error}`, де `error = {code, message, details?}`.
- `app/memory/__init__.py` — порожній.
- Тести, прив'язані до версії 1: `tests/test_database.py` (рядки 39–138), `tests/test_main.py` (`0 -> 1`, `== 1`), `tests/test_collector.py:325`. Два тести в `test_database.py` імітують «міграцію 2» через monkeypatch `MIGRATIONS`.

## 2. Суперечності й неоднозначності — потрібне твоє рішення

Біля кожного пункту — моя рекомендація, але без твого рішення я її не реалізую.

| # | Що | Суть | Рекомендація |
|---|---|---|---|
| **C1** | `tool_results` vs `ToolResult` | У `ToolResult` є поле `error` (`code`, `message`, `details`), у таблиці 2.5 — немає. Без нього для `partial` / `error` губиться причина збою. | Додати колонку `error` JSON nullable. `payload` = `ToolResult.data`. |
| **C2** | Ім'я `Observation` | D2: не дублювати `app/models/observation.py`. Але таблиця 2.2 — це інша сутність (запис у пам'яті інциденту з `source_ref`). Однакове ім'я класу плутатиме. | Таблиця `observations`, як у специфікації. Dataclass — `IncidentObservation` в `app/memory/models.py`. Старий `Observation` не змінюється. |
| **C3** | Інваріанти 3–5: M1 чи M4? | Розділ 6 відносить «правила статусів гіпотез (інваріанти 3–5)» до **M4**. Ти просиш інваріанти в M1. | M1 — **запобіжники**: repository відхиляє недозволений перехід (хто, з якого статусу, чи виконано умови 4–5). M4 — **правила**, які самі вирішують, коли змінити статус. |
| **C4** | Інваріант 6 у M1 | «Knowledge `failed` не повертається retrieval як рішення» — це про retrieval (M7), якого в M1 немає. | У M1 два методи читання: `list_recommendable_knowledge()` (без `failed` / `deprecated`) і `list_knowledge_warnings()` (лише `failed`). Тест інваріанту — на них. M7 мусить користуватися ними. |
| **C5** | Автоміграція реальної БД | Після злиття M1 **перший же** `main.py collect` / `analyze` / dashboard викличе `ensure_schema()` і мовчки мігрує `data/juli.db` до v2 без резервної копії. Це суперечить правилу «перед міграцією — копія». | `ensure_schema()` перед оновленням **наявної** бази (версія ≥ 1, файл існує) робить копію `<db>.backup-YYYYMMDD-v<old>` через `sqlite3` backup API. Наявну копію не перезаписує. Альтернатива: інші команди відмовляються працювати, поки не виконано `init-db`. Але це зміна CLI, а CLI в M1 не чіпаємо. |
| **C6** | Foreign keys | FK зі специфікації в SQLite не діють без `PRAGMA foreign_keys = ON` на кожному з'єднанні. | Вмикати в `get_connection()` для всіх. На `system_snapshots` це не впливає (там FK немає). |
| **C7** | Актори | `memory_events.actor` ∈ `rule / llm / user`. Але є також `origin = memory`, `initiator = memory_reverification`, `opened_by = juli_suggested`. | `actor` лишається `rule / llm / user`. Створення гіпотези з `origin = memory` — `actor = rule` (детермінований retrieval). `opened_by = juli_suggested` — `actor = user`, бо D1: відкриває користувач. |
| **C8** | Хто що може змінювати | Правило «LLM не ставить `verified` / `supported` / `refuted`» не охоплює `inconclusive`, `failed` (knowledge), статуси інциденту та `approval`. | Консервативна матриця (розділ 4.3): LLM може лише **створити** (гіпотезу `proposed`, зв'язок `asserted`, дію `planned`) і перевести гіпотезу `proposed → testing`. Усе інше — `rule` або `user`. `approval` `approved` / `rejected` — тільки `user`, бо це принцип людського погодження. |
| **C9** | «Підтвердження користувача» (інв. 4) | Не визначено, як воно зберігається. | Evidence з `source_type = user`, `assessed_by = user` і зв'язком `supports` (`created_by = user`). |
| **C10** | «Нова перевірка в поточному інциденті» (інв. 5) | Не визначено, що таке «нова». | Для `origin = memory`: потрібен `supports` від evidence з `source_type = tool_result`, яке належить **тому самому** інциденту і створене **не раніше** за гіпотезу. Підтвердження лише користувачем тут **не** зараховується, бо «Tool Layer перевіряє». |
| **C11** | Переходи, не описані в специфікації | Описано лише для incidents і частково для hypotheses. | Пропозиція — розділ 4.2. Окремо питання: чи може `supported` / `refuted` / `inconclusive` повернутися в `testing` (напр., з'явився новий `contradicts`)? Рекомендую так, із записом у журнал. |
| **C12** | «statement англійською» | Мову надійно перевірити кодом не можна. | Мінімальний запобіжник: `statement` (observations, hypotheses, knowledge) і `evidence.summary` не можуть містити кирилицю → `ValueError`. `symptom_text` — слова користувача, будь-якою мовою. |
| **C13** | `payload` у tool_results | Ліміт і санітизація — відкрите питання 8.2, логічно це M3. | У M1 — лише збереження, без санітизації. Payload більший за 64 KiB **відхиляється** з помилкою (без тихого обрізання). Остаточний ліміт — у M3. |
| **C14** | `root_cause_hypothesis_id` | Умови не задані. | Можна встановити лише на гіпотезу **цього ж** інциденту зі статусом `supported`; перевіряється під час закриття інциденту як `resolved`. |

## 3. Міграція 2

`_migration_2_memory(connection)` у `app/core/database.py`, лише `connection.execute` (по одному `CREATE` на виклик), у тій самій транзакції, що й `user_version = 2`. `system_snapshots` не змінюється.

- 9 таблиць з розділу 2 (`incidents`, `observations`, `hypotheses`, `diagnostic_actions`, `tool_results`, `evidence`, `memory_relations`, `knowledge`, `memory_events`). Поля й типи як у специфікації; JSON — `TEXT` з `CHECK (json_valid(...))`.
- Переліки статусів і типів — `CHECK (x IN (...))` як другий рубіж; основна перевірка в Python.
- FK: `incident_id`, `hypothesis_id`, `origin_knowledge_id`, `tool_result_id`, `basis_evidence_id`, `superseded_by`, `root_cause_hypothesis_id`. `ON DELETE` не задаю: видалення в M1 немає.
- Поліморфні посилання (`src_type / src_id`, `source_type / source_ref`, `entity_type / entity_id`) зовнішніми ключами не перевіряються, тому repository перевіряє, що запис існує (для `snapshot` — `system_snapshots.id`).
- Індекси: `incident_id` у дочірніх таблицях, `(src_type, src_id)`, `(dst_type, dst_id)`, `(entity_type, entity_id)`, `knowledge.status`.
- Колонка `error` у `tool_results` — тільки якщо погодиш C1.

## 4. Код у `app/memory/`

### 4.1. Файли

| Файл | Зміст |
|---|---|
| `models.py` | переліки (`Literal` + кортежі значень); frozen dataclasses: `Incident`, `IncidentObservation`, `Hypothesis`, `DiagnosticAction`, `StoredToolResult`, `Evidence`, `MemoryRelation`, `Knowledge`, `MemoryEvent`; валідація полів у `__post_init__` (переліки, ISO 8601 UTC, скінченні числа, C12); `from_row()` |
| `ids.py` | `new_id(kind)` → `inc_`, `obs_`, `hyp_`, `act_`, `tr_`, `ev_`, `rel_`, `kn_`, `evt_` + `uuid4().hex` |
| `transitions.py` | чисті функції без БД: дозволені переходи і права акторів (4.2, 4.3) |
| `repository.py` | `MemoryRepository`: `create_*`, `get_*`, `list_*_for_incident`, `change_status(...)`, `link(...)`, читання knowledge (C4). Кожна зміна разом із записом у `memory_events` — **в одній транзакції**. Час береться з годинника, який можна підмінити в тестах |
| `errors.py` | `MemoryError` → `InvalidTransition`, `ActorNotAllowed`, `InvariantViolation`, `NotFound` |
| `__init__.py` | публічні імена |

### 4.2. Переходи статусів (пропозиція, C11)

| Сутність | Дозволено |
|---|---|
| incident | `open → investigating`; `investigating → resolved \| unresolved`; `resolved \| unresolved → investigating` (подія `reopened`) |
| hypothesis | `proposed → testing`; `testing → supported \| refuted \| inconclusive`; `supported \| refuted \| inconclusive → testing` (якщо погодиш C11) |
| diagnostic_action.status | `planned → running \| skipped`; `running → succeeded \| failed` |
| diagnostic_action.approval | `pending → approved \| rejected`; `not_required` змінити не можна |
| memory_relation | `asserted → verified \| refuted` |
| knowledge | `unverified → verified \| failed`; `verified ↔ failed`; будь-який → `deprecated` (кінцевий) |

Початкові статуси фіксовані: incident `open`, hypothesis `proposed`, action `planned`, relation `asserted` (`verified` одразу при створенні — лише `rule` / `user` з дотриманням інв. 1), knowledge — будь-який, крім `deprecated`, але тільки від `rule` / `user`.

### 4.3. Права акторів (C8)

| Дія | `llm` | `rule` | `user` |
|---|---|---|---|
| створити гіпотезу (`proposed`), зв'язок (`asserted`), дію (`planned`), observation | ✅ | ✅ | ✅ |
| hypothesis `proposed → testing` | ✅ | ✅ | ✅ |
| hypothesis → `supported` / `refuted` / `inconclusive`, будь-що → `testing` з кінцевого | ❌ | ✅ | ✅ |
| relation → `verified` / `refuted` | ❌ | ✅ | ✅ |
| knowledge: створити / змінити статус | ❌ | ✅ | ✅ |
| incident: відкрити | ❌ | ❌ | ✅ |
| incident: змінити статус | ❌ | ✅ | ✅ |
| action approval `approved` / `rejected` | ❌ | ❌ | ✅ |
| action status (`running`, `succeeded`, …) | ❌ | ✅ | ✅ |
| evidence: створити | ✅ (`assessed_by = llm`) | ✅ | ✅ |

### 4.4. Інваріанти в коді

| # | Де перевіряється |
|---|---|
| 1 | створення зв'язку і `→ verified`: `causal` + `verified` без `basis_evidence_id` → `InvariantViolation`; `basis_evidence_id` має існувати |
| 2 | створення зв'язку: `similar_to` лише з `relation_class = associative`; перевірка `→ supported` рахує тільки `supports`, тож `similar_to` на неї не впливає |
| 3 | матриця 4.3, `ActorNotAllowed` |
| 4 | `→ supported`: ≥ 1 `supports` (не `refuted`) від evidence з `source_type = tool_result` або підтвердження користувача (C9); жодного `contradicts` зі статусом ≠ `refuted` |
| 5 | `→ supported` для `origin = memory`: додатково C10 |
| 6 | методи C4 |
| 7 | `change_status` пише `status_changed` / `reopened` в одній транзакції; якщо запис події не вдався — відкат статусу. Також пишу `created` при створенні і `linked` при створенні зв'язку |

## 5. Тести

Нові файли: `tests/test_memory_migration.py`, `tests/test_memory_models.py`, `tests/test_memory_repository.py`, `tests/test_memory_invariants.py`. Усі на тимчасових БД через `JULI_DB_PATH`.

- **Міграція:** чиста БД → 2 (всі 9 таблиць); v1 з snapshots → 2, рядки й схема `system_snapshots` без змін; повторний `ensure_schema()` нічого не пише; збій у міграції 2 → відкат до 1 без жодної нової таблиці; версія 99 → відмова; FK увімкнені; CHECK відхиляє невідомий статус; резервна копія (якщо C5).
- **Копія реальної БД:** тест копіює `data/juli.db` у `tmp_path` і мігрує копію; якщо файлу немає (інша машина) — `skip`. Плюс одноразова ручна перевірка на копії в scratchpad зі звітом.
- **Інваріанти:** щонайменше один позитивний і один негативний тест на кожен з 1–7; для 3 — параметризація за матрицею 4.3.
- **Repository:** створення / читання кожної сутності; недозволені переходи; запис у журнал на кожну зміну (кількість подій = кількість змін; `old_value` / `new_value` / `actor` / `reason`); поліморфні посилання на неіснуючий запис; кирилиця в `statement` (C12).
- **Наявні тести:** очікувана версія `1` → `2` у `test_database.py`, `test_main.py`, `test_collector.py`; імітовану «міграцію 2» в monkeypatch-тестах — на `latest + 1`. Сенс тестів не змінюється.

Після реалізації: `.venv/bin/python -m pytest -q`, `git diff --check`, звіт у кінці цього файлу.

## 6. Реальна база

- `data/juli.db` **не мігрується** і не змінюється в M1. Перед будь-якою ручною перевіркою — свіжа резервна копія `data/juli.db.backup-20261107`, а перевірка йде на копії в scratchpad.
- Див. C5: без рішення там реальна база мігрує сама при першому запуску після злиття.

## 7. Що змінюється

- нові: `app/memory/{models,ids,transitions,repository,errors}.py`, 4 тестові файли;
- змінюються: `app/core/database.py` (міграція 2; FK — C6; копія — C5), `app/memory/__init__.py`, наявні тести версії (розділ 5);
- не змінюються: `app/agent/*`, `app/llm/*`, `main.py`, `app/dashboard.py`, `app/models/observation.py`, моніторинг;
- документація після реалізації: звіт тут; `README.md` / `AGENTS.md` (Implementation status: Memory — «partial: schema + repository, no lifecycle/LLM»).

## 8. Питання до тебе

1. **C1–C14** — погоджуєш рекомендації чи змінюєш? Найважливіші: C3 (межа M1 / M4), C5 (автоміграція реальної БД), C8 (права акторів), C11 (повернення з кінцевих статусів гіпотези).
2. Заголовок специфікації («чернетка для погодження») залишити як є чи позначити «погоджено 07.11.2026» окремим комітом?
3. Commit плану й специфікації — одним комітом `docs: add memory data model v1 and m1 plan`?

---

## 9. Рішення від 07.11.2026

Рекомендації C1–C14 погоджено з уточненнями (C5, C6, C8–C11). Підсумок перенесено в `docs/MEMORY_DATA_MODEL_v1.md`, розділ 9.

Зміни щодо розділу 2 цього плану:

- **C5:** якщо резервна копія не вдалась, міграцію не виконувати.
- **C9:** підтвердження користувача — подія `actor = user` з обов'язковим `reason`, а не evidence `source_type = user`.
- **C10:** tool_result має бути отримано дією цього інциденту **після** створення гіпотези.
- **C11:** повернення в `testing` — лише через новий доказ (`rule`) або `user`.
- **C4, C12, C13:** узято варіант, що **відповідає специфікації**, бо моя рекомендація змінювала її суть і мала бути спершу показана:
  - C4 — рекомендоване знання виключає лише `failed`, як інваріант 6 (без `deprecated`);
  - C12 — кирилиця перевіряється лише в `statement` (без `evidence.summary`);
  - C13 — payload понад 64 KiB **обрізається** (як у розд. 2.5), з явною позначкою `truncated`, а не відхиляється.
- Специфікація: заголовок «v1.0, погоджено 07.11.2026», нове рішення D6 (General Chat / Incident Chat, M5), розділ 9.
