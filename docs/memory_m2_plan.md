# Memory M2 — план: CLI інцидентів

*Стан: **реалізовано** (07.11.2026); Q1–Q6 погоджено як рекомендовано; звіт — `docs/memory_m2_report.md`.*

Обсяг (розд. 6 специфікації + твоє уточнення): `main.py incident open / list / show / close / reopen`. Переходи — лише дозволені специфікацією; кожна зміна — у `memory_events` з `actor = user`. LLM, AgentCore, dashboard — **не чіпаю** (M5).

## 1. Що є зараз

- `app/memory/repository.py` (M1) уже вміє все, що потрібно на рівні даних: `open_incident(..., actor, host_context, opened_by)`, `change_incident_status(..., actor, reason, resolution_summary)`, `get`, `list_incidents(status)`, `list_for_incident`, `list_events(entity_type, entity_id)`. Повторне відкриття пише подію `reopened`, закриття ставить `closed_at`.
- Переходи інциденту (розд. 2.1 + 9): `open → investigating`; `investigating → resolved | unresolved`; `resolved | unresolved → investigating`.
- `main.py`: argparse із підкомандами `init-db`, `collect`, `analyze`, `chat`. `main()` перехоплює `RuntimeError, ValueError, OSError, sqlite3.Error` → `error: …`, код 1. Помилки пам'яті (`MemoryModelError` — `NotFound`, `InvalidTransition`, …) успадковують `Exception` і зараз **не перехоплюються** — їх треба додати.
- Версії JULI в коді немає (немає `__version__`).
- Після M1 перший запуск будь-якої команди мігрує реальну `data/juli.db` до v2 з автоматичною копією. `incident …` теж викликатиме `ensure_schema()`.

## 2. Прогалини й питання — потрібне твоє рішення

| # | Що | Суть | Рекомендація |
|---|---|---|---|
| **Q1** | Немає команди `open → investigating` | Специфікація не дозволяє `open → resolved`, але в переліку команд немає переходу в `investigating`. Отже, `close` щойно відкритого інциденту завжди падатиме. | Додати явну команду **`incident start <id>`** (`open → investigating`, `actor = user`). Альтернатива — `close` сам робить два переходи (дві події). Але тоді в журналі з'явиться «investigating», якого людина не робила, тож не раджу. |
| **Q2** | «версія JULI» в `host_context` | Специфікація (2.1) хоче версію JULI, але її ніде не визначено. | Додати `__version__ = "0.2.0"` в `app/__init__.py` (0.1 — Stage 0, 0.2 — Memory M1–M2) і писати в `host_context` ще `schema_version`. Git-хеш не брати: це виклик зовнішньої програми, і поза git-checkout його немає. |
| **Q3** | Обсяг журналу в `show` | «журнал подій» — лише події самого інциденту чи й пов'язаних записів? | Інцидент **і** його observations, hypotheses, diagnostic_actions, evidence та зв'язки, що їх торкаються, у хронологічному порядку. Через CLI M2 таких записів ще не буде, але M3–M5 їх додадуть. Потрібен новий метод repository `list_incident_events(incident_id)`. |
| **Q4** | Довгі ID | `inc_` + 32 hex — незручно вводити. | Приймати повний id **або** унікальний префікс від 8 символів після `inc_` (як у git). Неоднозначний або не знайдений префікс → помилка зі списком збігів. Повний id показувати завжди. |
| **Q5** | `--summary` | У твоєму переліку `--summary` стоїть для обох варіантів `close`. | Обов'язковий і непорожній для `--resolved` і `--unresolved`. `--reason` для `reopen` теж обов'язковий. |
| **Q6** | Корінь причини при `close` | C14 дозволяє `root_cause_hypothesis_id` при `resolved`, але гіпотез через CLI в M2 ще немає. | У M2 **не додавати** `--root-cause`; з'явиться, коли гіпотези будуть доступні (M4/M5). |

## 3. Команди

Усі команди: `actor = user`, перед роботою `ensure_schema()`. Вивід англійською, як решта CLI. Коди виходу: 0 — успіх, 1 — помилка (без traceback), 2 — помилка аргументів (argparse).

```text
main.py incident open "<title>" --symptom "<text>"
main.py incident start <id>                                   # Q1
main.py incident list [--status open|investigating|resolved|unresolved]
main.py incident show <id>
main.py incident close <id> (--resolved | --unresolved) --summary "<text>"
main.py incident reopen <id> --reason "<text>"
```

| Команда | Дія в repository | Подія |
|---|---|---|
| `open` | `open_incident(title, symptom_text, actor="user", opened_by="user", host_context=collect_host_context())` | `created`, `new_value = open` |
| `start` | `change_incident_status(id, "investigating", "user")` | `status_changed open → investigating` |
| `close` | `change_incident_status(id, "resolved" \| "unresolved", "user", resolution_summary=…)` | `status_changed investigating → resolved \| unresolved` |
| `reopen` | `change_incident_status(id, "investigating", "user", reason=…)` | `reopened resolved \| unresolved → investigating`, з `reason` |
| `list`, `show` | лише читання | — |

Недозволений перехід (напр., `close` інциденту в стані `open`, `reopen` відкритого) → `error: InvalidTransition: …`, код 1, нічого не записано.

`title` і `symptom` — будь-якою мовою (це не `statement`, C12); порожні → помилка.

### `list`

```text
ID                                   STATUS         OPENED (UTC)          TITLE
inc_3f2a9c1e…                        investigating  2026-11-07 15:40:02   Disk almost full
```

Порядок — за часом відкриття. Порожній результат → `No incidents.` (з фільтром — `No incidents with status <s>.`).

### `show`

Блоки: заголовок (id, title, status, opened_by, created / updated / closed), symptom, resolution summary, host context, observations, hypotheses, events. Порожні блоки → `none`. Відсутні значення → `unknown`, не вигадуються.

## 4. `host_context` (збирається автоматично)

Нова функція `collect_host_context()` у `app/memory/host_context.py` — лише читання, без зовнішніх команд:

| Ключ | Джерело | Якщо недоступне |
|---|---|---|
| `hostname` | `platform.node()` | `null` |
| `os` | `PRETTY_NAME` з `/etc/os-release` | `null` |
| `kernel` | `platform.release()` | `null` |
| `architecture` | `platform.machine()` | `null` |
| `python` | `platform.python_version()` | `null` |
| `juli_version` | `app.__version__` (Q2) | — |
| `schema_version` | `get_schema_version()` | — |
| `collected_at` | час UTC | — |

Нічого не вигадується: невідоме значення — `null`. Примітка: `hostname` зберігається лише локально. Для датасету статті його доведеться санітизувати (відкрите питання 8.2, M3).

## 5. Файли

| Файл | Зміна |
|---|---|
| `app/memory/incident_cli.py` (новий) | `add_incident_parser(subparsers)`, обробники команд, форматування `list` / `show`, розв'язання id-префіксів (Q4). Окремий модуль, щоб `main.py` не розростався |
| `app/memory/host_context.py` (новий) | `collect_host_context()`; джерела можна підмінити в тестах |
| `app/memory/repository.py` | `list_incident_events(incident_id)` (Q3); `find_incidents_by_prefix(prefix)` (Q4) |
| `main.py` | реєстрація `incident`; у `main()` перехоплювати також `MemoryModelError` |
| `app/__init__.py` | `__version__` (Q2) |
| `tests/test_incident_cli.py` (новий) | див. розд. 6 |
| `README.md`, `AGENTS.md` | команди й статус M2 |
| `docs/memory_m2_report.md` | звіт після реалізації |

Не змінюються: `app/agent/*`, `app/llm/*`, `app/dashboard.py`, моніторинг, схема БД (міграція не потрібна).

## 6. Тести

Усі на тимчасових БД (запобіжник із `conftest.py` діє на кожен тест).

- `open`: друкує id; запис має `status = open`, `opened_by = user`, `host_context` із підміненими джерелами; подія `created` з `actor = user`; порожні title / symptom → код 1.
- `host_context`: усі ключі присутні; недоступні джерела (немає `/etc/os-release`, `platform.node()` = `""`) → `null`, без падіння.
- `start` / `close` / `reopen`: повний цикл `open → investigating → resolved → investigating → unresolved`; кожна зміна — рівно одна подія з `actor = user`, правильними `old` / `new`, `reason` для `reopen`; `closed_at` і `resolution_summary` після `close`, `closed_at = null` після `reopen`.
- Недозволені переходи: `close` з `open`; `reopen` з `open` / `investigating`; `start` двічі → код 1, статус і журнал без змін.
- Аргументи: `close` без `--resolved` / `--unresolved`, з обома, без `--summary`; `reopen` без `--reason` → код 2 (argparse); порожній текст → код 1.
- `list`: усі; фільтр за статусом; невідомий статус → код 2; порожня база.
- `show`: усі блоки; події в хронологічному порядку; записи, додані через repository (observation, hypothesis), видно в `show`.
- id: повний id, унікальний префікс, неоднозначний префікс, неіснуючий → код 1 з поясненням.
- `incident` не імпортує і не викликає LLM: тест підміняє `OllamaProvider` / `AgentCore` на «вибухаючі» заглушки.
- Наявні тести не змінюються.

Після реалізації: `pytest -q`, `git diff --check`, звіт `docs/memory_m2_report.md`.

## 7. Реальна база

- Тести й перевірки — лише на тимчасових БД або копії.
- Ручна перевірка — на копії `data/juli.db` у scratchpad (`JULI_DB_PATH`).
- Перший запуск `incident …` на реальній базі мігрує її до v2 (з автоматичною копією `data/juli.db.backup-YYYYMMDD-v1`), як і будь-яка інша команда після M1. Сама я `incident` на реальній базі не запускатиму.

## 8. Питання до тебе

Q1–Q6 — погоджуєш рекомендації? Найважливіше — **Q1** (без переходу в `investigating` команда `close` не працюватиме) і **Q2** (номер версії JULI).
