# Завдання 6 — план: документація і залежності

*Стан: **реалізовано** (07.11.2026). План погоджено 07.11.2026; звіт — у кінці файлу.*

## 1. Поточний стан (перевірено в checkout `32c9adb`)

### 1.1 Залежності

- `requirements.txt` — один рядок: `psutil==7.2.2`. `streamlit` і `pandas` не вказані, хоча `app/dashboard.py` їх імпортує.
- `requirements-dev.txt` — немає.
- Сторонні імпорти в `app/`, `main.py`, `dataset_collection/`, `tests/` (решта — стандартна бібліотека):

| Пакет | Де імпортується | Версія в `.venv` |
|---|---|---|
| `psutil` | `app/tools/*`, `dataset_collection/collect.py` | 7.2.2 |
| `streamlit` | `app/dashboard.py` | 1.65.0 |
| `pandas` | `app/dashboard.py` | 3.0.6 |
| `pytest` | `tests/*` | 9.1.1 |

- Ollama-провайдер працює тільки на `urllib` (стандартна бібліотека), тому `requests` (є в `.venv` як транзитивна залежність streamlit) у requirements **не додається**. `altair` — так само транзитивна залежність streamlit.
- Python у `.venv`: 3.12.3. Код використовує `X | None`, тобто мінімум Python 3.10.

### 1.2 README.md

Файл порожній (0 байт).

### 1.3 AGENTS.md → Implementation status

Знімок старого аудиту: вказує, що Agent Core, повний entry point, конфіг тощо не реалізовані, і «26 declared test cases». Фактично зараз:

- `pytest -q`: **347 passed, 47 subtests passed** (запуск 07.11.2026, 1.51 с), 17 тестових файлів.
- Реалізовано після аудиту (етап 0, завдання 1–5): уніфікований `ToolResult` і `partial` для `get_system_state`; живучий Collector; `app/config.py` зі змінними `JULI_*`; `ensure_schema()` з `PRAGMA user_version` (міграція 1); `main.py` (`init-db`, `collect`, `analyze`, `analyze --context`, `chat`); freshness / current / recent / historical у моніторингу; Context Builder; `AgentCore` + Ollama-провайдер (`qwen3.5:4b`, `think=false`, `num_ctx`, `temperature`, `seed`, `usage`).
- Реєстр: 7 read-only інструментів, але лише з **2 із 7 категорій** (system, processes). `app/tools/services/` порожній; Network, Containers, Filesystem, Logs — немає. Action Tools і Risk Analyzer — немає.
- `app/memory/` — порожній; Incident Management — немає.

### 1.4 `app/monitoring/analysis_service.py.bak`

- Не відстежується git (`??`), ніде не згадується в коді, 30 рядків, 903 байти, змінений 14.10.2026.
- **Вміст:** рання версія `analyze_system_history(limit, min_samples, max_gap_seconds)`: читає snapshots через `load_recent_snapshots`, викликає `detect_persistent_anomalies` і повертає `{status: "success", snapshots_analyzed, anomalies_found, anomalies}`. Без threshold-аномалій, PSI, significance, data issues, валідації параметрів і без freshness.
- **Різниця з поточною версією** (551 рядок, 541 змінений рядок у diff): поточний файл — це вже `run_monitoring_cycle()` з усім вищезгаданим, а `analyze_system_history()` лишився сумісною обгорткою, що повертає **ті самі старі ключі** (`anomalies`, `anomalies_found`, `snapshots_analyzed`) плюс нові. Тобто нічого з `.bak` не втрачено — і вся його логіка є підмножиною поточної.
- Повний diff покажу командою `diff -u app/monitoring/analysis_service.py.bak app/monitoring/analysis_service.py` у звіті; там він не потрібен дослівно.
- **Рекомендація:** видалити (у git його немає, отже це безповоротно — тільки з твого окремого «так»). Альтернатива — залишити, але додати `*.bak` у `.gitignore`.

### 1.5 Інше, помічене дорогою (не змінюю без погодження)

- `output/` у корені — не відстежується, містить `output/sanitized/processes_20261011T181725Z_sanitized.json` (результат `dataset_collection`). Пропоную лише додати `output/` у `.gitignore`; файл не чіпаю.

## 2. Що зробити

### 2.1 `requirements.txt` і `requirements-dev.txt`

```text
# requirements.txt — runtime dependencies (Python >= 3.10)
psutil==7.2.2
streamlit==1.65.0
pandas==3.0.6
```

```text
# requirements-dev.txt
-r requirements.txt
pytest==9.1.1
```

Пакети не встановлюю — версії взято з наявного `.venv` (`pip freeze`).

### 2.2 `README.md` (англійською, як код; або українською — див. питання нижче)

Розділи:

1. **What is JULI** — 3–5 речень: локальний агент діагностики Linux, Observe → Understand → Act, LLM без shell, read-only інструменти, людське погодження небезпечних дій (як принцип; ще не реалізовано).
2. **Status** — коротко: етап 0 (стабілізація) завершено; Memory, Incident Management, Action Tools — ще ні. Посилання на `AGENTS.md` і `docs/STAGE_0_STABILIZATION.md`.
3. **Requirements** — Linux (PSI з `/proc/pressure/*`, якщо ядро підтримує), Python ≥ 3.10, Ollama (тільки для `chat`).
4. **Installation** — `python3 -m venv .venv`, `.venv/bin/pip install -r requirements.txt` (або `-dev`), `main.py init-db`.
5. **Ollama** — встановлення, `ollama pull qwen3.5:4b`, `ollama list`; чому `think=false` за замовчуванням; як змінити модель.
6. **Commands** — `init-db`, `collect [--interval N]`, `analyze`, `analyze --context`, `chat` (`/context`, `/clear`, `/exit`) з прикладами.
7. **Dashboard** — `.venv/bin/streamlit run app/dashboard.py` (перевірю, чи потрібен `PYTHONPATH=.` для імпорту `app.*`, і впишу точну робочу команду; запуск тільки для перевірки імпорту, без змін БД окрім `ensure_schema()`, яка на наявній базі нічого не міняє).
8. **Tests** — `.venv/bin/python -m pytest -q` з кореня.
9. **Configuration** — таблиця всіх змінних `JULI_*` з типом, значенням за замовчуванням і валідацією:
   `JULI_DB_PATH`, `JULI_COLLECTOR_INTERVAL_SECONDS` (10), `JULI_RECENT_WINDOW_MINUTES` (15), `JULI_MONITORING_CONTEXT_MAX_CHARS` (4000), `JULI_OLLAMA_HOST` (`http://127.0.0.1:11434`), `JULI_OLLAMA_MODEL` (`qwen3.5:4b`), `JULI_OLLAMA_TIMEOUT_SECONDS` (120), `JULI_OLLAMA_THINK` (false), `JULI_OLLAMA_NUM_CTX` (16384, ≥ 2048), `JULI_OLLAMA_TEMPERATURE` (0.2, 0–2), `JULI_OLLAMA_SEED` (не задано).
   Значення візьму з коду (`app/config.py`, `app/llm/config.py`), а не з цього плану.
10. **Project layout** — коротке дерево `app/` з одним рядком на пакет; `dataset_collection/` — окремий набір скриптів.
11. **Data** — `data/juli.db` не в git; резервні копії `data/juli.db.backup-YYYYMMDD`.

### 2.3 `AGENTS.md` → Implementation status

Переписати лише цей розділ (решту AGENTS.md не чіпаю), у трьох групах, з датою і посиланням на commit:

- **Implemented** — перелік з 1.3 + усе зі старого списку, що досі актуальне (history reader, threshold/temporal analysis, PSI, significance evaluation / Evidence Profiles, dataset utilities, Streamlit dashboard).
- **Partially implemented** — Tool Layer (7 інструментів, 2 з 7 категорій); Monitoring (threshold + temporal + PSI; baseline, USE, Four Golden Signals — ні); Agent Core (тільки діалог з контекстом моніторингу, без Diagnostic Planner і без виклику інструментів моделлю); Collector (ручний запуск, без systemd).
- **Not implemented** — Incident Management, Contextual та Long-term Memory (`app/memory/` порожній), Action Tools, Risk Analyzer, Diagnostic Planner, Outcome / Evaluation / Knowledge Extraction.
- Тести: точна кількість з фактичного запуску в момент редагування.

Залишу примітку, що це знімок і перед використанням треба перевіряти код (як зараз). Відповідну примітку в `CLAUDE.md` («Implementation status is an old snapshot») пропоную потім оновити — але `CLAUDE.md` в `.gitignore`, тож це локальна зміна; зроблю тільки якщо скажеш.

### 2.4 `.bak` і `.gitignore`

- `.bak`: на твоє рішення — видалити або залишити.
- `.gitignore`: пропоную додати `*.bak` та `output/`.

## 3. Тести і перевірка

Код не змінюється, тому нових тестів немає. Після змін:

1. `.venv/bin/python -m pytest -q` — очікується той самий результат (347 passed).
2. `git diff --check`.
3. Перевірка requirements без встановлення: `.venv/bin/pip install --dry-run -r requirements-dev.txt` (лише резолвить, нічого не ставить — або, якщо хочеш зовсім без мережі, порівняння з `pip freeze`).
4. Кожну команду з README виконати «вхолосту»: `main.py --help`, `main.py analyze --help`, `main.py init-db` **на тимчасовій БД** (`JULI_DB_PATH` у scratchpad), імпорт dashboard. Реальну `data/juli.db` не чіпаю.

## 4. Порядок і commit

1. requirements → 2. README → 3. AGENTS.md → 4. `.gitignore` / `.bak` → перевірка → звіт у кінці цього файлу.
Commit (`docs: add readme, pin dependencies and update implementation status`) — тільки після твого дозволу.

## 5. Питання до тебе

1. Мова README: англійська (як код і commit-и) чи українська?
2. `analysis_service.py.bak`: видаляти?
3. Додати `*.bak` і `output/` у `.gitignore`?

---

## 6. Звіт (07.11.2026)

Відповіді на питання з розділу 5: README — англійською; `.bak` не видаляти, а перемістити в `~/juli_archive/`; `*.bak` і `output/` — у `.gitignore`, файл в `output/` не видаляти.

### Змінені файли

| Файл | Що зроблено |
|---|---|
| `requirements.txt` | `psutil==7.2.2`, `streamlit==1.65.0`, `pandas==3.0.6` |
| `requirements-dev.txt` (новий) | `-r requirements.txt`, `pytest==9.1.1` |
| `README.md` | з порожнього файлу: опис, статус у трьох групах, встановлення, Ollama, команди, dashboard, тести, 11 змінних `JULI_*`, структура |
| `AGENTS.md` | переписано лише розділ Implementation status (implemented / partial / not implemented + фактичні тести) |
| `.gitignore` | `*.bak`, `/output/` |
| `app/monitoring/analysis_service.py.bak` | переміщено в `~/juli_archive/analysis_service.py.bak` (903 байти, без змін) |

### Відхилення від плану і знахідки

- `.gitignore`: `/output/` замість `output/`. Шаблон без `/` діє на будь-якому рівні. `dataset_collection/output/` має власну `.gitignore`, тож зараз результат той самий, але `/output/` точно означає лише корінь. `output/sanitized/…_sanitized.json` залишено на місці, тепер він ігнорується.
- Dashboard: `.venv/bin/streamlit run app/dashboard.py` **не працює** (`ModuleNotFoundError: No module named 'app'`), бо streamlit додає в `sys.path` лише `app/`. Робоча команда — `.venv/bin/python -m streamlit run app/dashboard.py` з кореня. Так і записано в README. Код не змінювався.

### Перевірка

- Requirements: `pip install --dry-run --no-index -r requirements-dev.txt` — усі 4 пакети `already satisfied`, нічого не встановлено.
- CLI на тимчасовій БД (`JULI_DB_PATH` у scratchpad): `--help`, `init-db` (`Schema version: 0 -> 1`), `collect --interval 5` ~25 с (4 snapshots), `analyze` (`fresh`, age 6 s), `analyze --context` (1550 символів), `chat --help`. Сам `chat` з моделлю не запускався.
- Dashboard: `streamlit.testing.AppTest` на тимчасовій БД — без винятків, заголовок `🌼 JULI`, попередження `No system snapshots available.`
- Реальна `data/juli.db` не змінювалась (mtime 03.11.2026 17:25).
- `pytest -q` і `git diff --check` — див. підсумок у чаті.
