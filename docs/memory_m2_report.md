# Memory M2 — звіт: CLI інцидентів

*07.11.2026. Крок M2 з `docs/MEMORY_DATA_MODEL_v1.md` (розділ 6). План — `docs/memory_m2_plan.md` (погоджено 07.11.2026, Q1–Q6 — як рекомендовано).*

## 1. Коротко

- `main.py incident open / start / list / show / close / reopen`. Кожна команда діє як `actor = user`; кожна зміна статусу — рівно одна подія в `memory_events`.
- Дозволені лише переходи специфікації: `open → investigating` (`start`), `investigating → resolved | unresolved` (`close`), `resolved | unresolved → investigating` (`reopen`, подія `reopened` з обов'язковою причиною).
- `host_context` збирається автоматично (hostname, OS, kernel, architecture, Python, `juli_version` 0.2.0, `schema_version`, час).
- LLM, AgentCore, dashboard, моніторинг і схема БД не змінювались.
- Реальна `data/juli.db` **контрольовано мігрована до v2** перед початком M2 (розд. 4).
- `pytest -q` → **532 passed, 47 subtests passed** (23 тестові файли). `git diff --check` — без зауважень.

## 2. Що зроблено

| Файл | Що |
|---|---|
| `app/memory/incident_cli.py` (новий) | підкоманди `incident`, обробники, форматування `list` / `show` |
| `app/memory/host_context.py` (новий) | `collect_host_context()` — лише `platform` і `/etc/os-release`, без зовнішніх команд; невідоме → `null` |
| `app/memory/repository.py` | `list_incident_events(id)` — хронологія подій інциденту і всіх пов'язаних записів (Q3); `resolve_incident_id(ref)` — повний id або унікальний префікс (Q4) |
| `main.py` | реєстрація `incident`; `main()` перехоплює також `MemoryModelError` → `error: …`, код 1, без traceback |
| `app/__init__.py` | `__version__ = "0.2.0"` (Q2) |
| `tests/test_incident_cli.py` (новий) | 27 тестів (розд. 5) |
| `tests/test_memory_migration.py` | тест копії реальної БД більше не вважає, що вона на v1 (розд. 4) |
| `README.md`, `AGENTS.md` | команди `incident`, статус M2 |

## 3. Рішення Q1–Q6 і деталі

| # | Рішення |
|---|---|
| Q1 | окрема команда `incident start <id>` (`open → investigating`). `close` відкритого інциденту → помилка з підказкою `main.py incident start <id>` |
| Q2 | `app.__version__ = "0.2.0"`; у `host_context` — `juli_version` і `schema_version` |
| Q3 | `show` показує події інциденту + його observations, hypotheses, diagnostic_actions, evidence, tool results цих дій і зв'язків, що їх торкаються; порядок — час, потім порядок запису |
| Q4 | повний id або унікальний префікс від 8 hex-символів (`inc_` необов'язковий); неоднозначний → помилка зі списком усіх збігів; закороткий / не знайдений → помилка |
| Q5 | `--summary` обов'язковий для `--resolved` і `--unresolved`; `--reason` обов'язковий для `reopen`; порожній текст → помилка, нічого не записано |
| Q6 | `--root-cause` у M2 немає (M4 / M5) |

Додатково (не описано в плані явно):

- `start` для закритого інциденту і `reopen` для незакритого відхиляються ще в CLI. Repository дозволяє `resolved → investigating` як перехід, тож без цієї перевірки `start` обійшов би обов'язкову причину `reopen`, а `reopen` відкритого інциденту спрацював би як `start`.
- Після `reopen` попередній `resolution_summary` лишається до наступного `close` (як у M1); `closed_at` очищається.
- Вивід CLI — англійською, як решта `main.py`; `title` і `symptom` — будь-якою мовою (C12 стосується лише `statement`).

## 4. Реальна база: контрольована міграція до v2

Виконано **перед** реалізацією M2, з твого дозволу.

1. Еталон із реальної бази (лише читання, `mode=ro`): `user_version` 1, `integrity_check` ok, `system_snapshots` 1393 рядки, `sqlite_sequence` 1 рядок, sha256 файлу `93e211d2…86ea`, хеш вмісту snapshots `f178d81b…57a1`. Процесів JULI не було.
2. Репетиція на копії в scratchpad показала, що файл резервної копії відрізняється від оригіналу рівно 4 байтами заголовка (зміщення 24–27 і 92–95 — лічильник змін і version-valid-for, SQLite backup API скидає їх до 1); тому sha256 копії інший (`f605e43f…b9b4`), а вміст — той самий. Ти підтвердила, що критерій sha256 був неправильним і перевіряти треба за вмістом.
3. `main.py init-db` на реальній базі о **16:00:17 UTC** → `Schema version: 1 -> 2`; створено `data/juli.db.backup-20261107-v1`.
4. Перевірка копії за вмістом проти еталону:

| Перевірка | Результат |
|---|---|
| `user_version` | 1 ✅ |
| `PRAGMA integrity_check` | ok ✅ |
| кількість рядків у кожній таблиці | `system_snapshots` 1393, `sqlite_sequence` 1 ✅ |
| `sqlite_sequence`, схема і вміст `system_snapshots` | збігаються ✅ |

5. Мігрована `data/juli.db`: версія 2, `integrity_check` ok, 1393 snapshots з тим самим вмістом, 9 порожніх таблиць пам'яті.

Повторна перевірка (лише читання) після твого `main.py chat`: chat (PID 12477) стартував о **16:00:22**, на 5 с пізніше за міграцію, застав v2 і повторно не мігрував; інших резервних копій немає; snapshots — 1393, таблиці пам'яті порожні. Сама я `incident …` на реальній базі не запускала: ручна перевірка M2 — на копії в scratchpad (open → start → close `--unresolved`, `show` з host context і трьома подіями).

Наслідок для тестів: `test_copy_of_the_real_database_migrates_and_the_original_is_untouched` очікував резервну копію `…-v1`, бо вважав реальну базу версією 1. Тепер він очікує копію лише тоді, коли версія джерела нижча за останню. Шлях міграції `v1 → v2` і далі покривають синтетичні тести з базою версії 1.

## 5. Тести

`tests/test_incident_cli.py` (27):

- `open`: статус `open`, `opened_by = user`, `host_context`, подія `created` з `actor = user`; порожні title / symptom → код 1, нічого не створено; без `--symptom` → код 2.
- Повний цикл `open → investigating → resolved → investigating → unresolved`: рівно 5 подій, усі `actor = user`, правильні `old` / `new`, `reason` у `reopened`; `closed_at` і `resolution_summary`.
- Недозволені переходи (6 випадків): код 1, `InvalidTransition`, без traceback, статус і журнал без змін.
- Обов'язкові опції `close` / `reopen` → код 2; порожні `--summary` / `--reason` → код 1, журнал без змін.
- `list`: усі, фільтр, невідомий статус → код 2, порожня база.
- `show`: усі блоки, observation і hypothesis, додані через repository, хронологія, події іншого інциденту не потрапляють.
- id: повний, префікс з `inc_` і без, закороткий, не знайдений, неоднозначний (зі списком збігів).
- `host_context`: значення з підмінених джерел; недоступні джерела → `null`, без падіння; реальний збір має всі ключі.
- Команди `incident` не створюють `AgentCore` і `OllamaProvider` (підмінені «вибухаючими» заглушками).

## 6. Окремий коміт: ім'я JULI у промпті

На твоє прохання під час M2 — коміт `d21a71c` (`app/agent/core.py`, `tests/test_agent_core.py`): правило «ім'я — просто JULI, не розшифровувати, не вигадувати абревіатур; на питання про ім'я — відповідати JULI». Розшифровку модель не вигадала: вона стояла в першому рядку самого промпту (`You are JULI (Just Understanding Light Inside)`), тож її прибрано звідти. Згодом, на твоє прохання, розшифровку прибрано й з `AGENTS.md` і `README.md`: ім'я — просто JULI.

## 7. Обмеження

- Через CLI в інцидент поки нічого не додається, крім статусів: observations, гіпотези, перевірки — M3–M5.
- `hostname` зберігається в `host_context`; для датасету статті його доведеться санітизувати (відкрите питання 8.2).
- Запущений `main.py chat` (PID 12477) використовує старий системний промпт, доки його не перезапустити.
