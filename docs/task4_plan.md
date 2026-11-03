# Завдання 4 — план: Context Builder

*Стан: **реалізовано** (03.11.2026). Відхилення від плану:*

1. *Після стискання PSI і поточних значень спорожнілі блоки 7, 6, 4, 3 теж стискаються до одного рядка (`Historical: N items omitted.` тощо). Так ліміт гарантовано виконується на мінімальному `max_chars`.*
2. *Ліміт 10 у блоці 3 застосовується окремо до стійких серій і до threshold-аномалій; серії йдуть першими.*
3. *`AgentCore` повертається до 4000 не тільки при `ValueError`, а й коли `JULI_MONITORING_CONTEXT_MAX_CHARS` — додатне ціле `< 1000`: конфіг таке значення приймає, а builder ні.*
4. *Ліміти рядків розрізняються за полем: timestamp — 35, назви в переліках — 40, типи issues — 60, решта — 120 (у плані було 120 для всіх).*

## 1. Поточна реалізація

`AgentCore.handle_message()` (`app/agent/core.py`) додає до промпту друге системне повідомлення:

```
"Current structured Monitoring Analysis (JSON evidence):\n" + _compact_json(monitoring_result)
```

`_compact_json` серіалізує **весь** результат `run_monitoring_cycle()` без жодного відбору.

**Вимір на копії реальної бази** (`data/juli.db` → scratchpad, 03.11.2026; стан `stale`, 0 recent, 4 historical):

| Ключ результату | Символів у JSON |
|---|---|
| `significance_assessments` (4 профілі) | 18 548 |
| `pressure_observations` (18 PSI) | 3 619 |
| `threshold_anomalies` | 773 |
| `data_issues` | 445 |
| `historical` / `recent` / `freshness` / `current` | 310 / 215 / 149 / 102 |
| **Разом** | **24 661** |

Основний об'єм дає `corroborating_evidence.psi_context.observations`: у кожному профілі повна копія тих самих 18 PSI-спостережень. До того ж дані про аномалії повторюються тричі: верхній рівень, `recent` і всередині профілів. Розмір росте лінійно з кількістю аномалій.

**Хто використовує `AgentCore`:** `app/dashboard.py` (чат) і `tests/test_agent_core.py`. Два тести перевіряють саме JSON-формат: `'"metric":"psi_cpu"' in content` і `json.loads(...)` для NaN. Їх доведеться оновити.

**Що вже є і що використаємо:**
- `config.get_monitoring_context_max_chars()` → 4000 (`JULI_MONITORING_CONTEXT_MAX_CHARS`);
- `freshness`, `current`, `recent`, `historical` із Завдання 3;
- PSI є на верхньому рівні (`pressure_observations`). Context Builder бере його **тільки звідти** і ніколи не читає `psi_context` з профілів. `significance_evaluator.py` не змінюється.

## 2. Новий модуль `app/agent/context_builder.py`

```python
def build_monitoring_context(result: dict | None, max_chars: int | None = None) -> str
```

- `max_chars=None` → `get_monitoring_context_max_chars()`. Значення має бути `int` (не `bool`) і `>= MIN_CONTEXT_CHARS` (пропоную 1000), інакше `ValueError`. Мінімум гарантує, що необрізні блоки 1 і 8 завжди вміщуються.
- Чиста детермінована функція: вхід не змінюється, той самий вхід дає той самий текст, без звернень до БД, часу чи мережі.
- Текст англійською, як і системний промпт: llama3.2 краще тримає англійські інструкції. Формат — простий текст із заголовками блоків, а не JSON.
- Терпимість до неповного входу: `None`, не-dict, відсутні ключі (старий формат), `None`/NaN/inf у значеннях дають `unknown`, а не `0`. Незнайомі об'єкти (наприклад, `Observation`) читаються через атрибути або ключі.

### 2.1 Блоки (за пріоритетом)

| # | Блок | Зміст | Обрізання |
|---|---|---|---|
| 1 | Freshness | **Перший рядок.** `Data freshness: fresh/stale/no_data/unknown`, timestamp, вік (людською мовою: «3.2 days»), ліміт. При `stale`/`no_data`/`unknown`: `CURRENT SYSTEM STATE IS UNKNOWN; values below are the last stored measurements, not the present state.` При `snapshot_in_future`: окреме попередження про ненадійний годинник. | ніколи |
| 2 | Current values | `Latest snapshot (<ts>): CPU 12.3 %, RAM 45.0 %, Disk 60.1 %`. Відсутнє значення → `unknown`. Немає даних → `none available`. | ні (фіксований розмір) |
| 3 | Recent | Вікно (`last 15 min, since <window_start>`). Threshold-аномалії від найновішої, **не більше 10**: `- <ts> cpu_usage 97.2 % > 90.0 %`. Стійкі серії: `- cpu_usage > 90.0 % from <start> to <end>, 6 samples, max 98.1 %, span 50 s`. Якщо нічого немає: `none`. | 4-й у черзі |
| 4 | Significance (recent) | Один рядок на профіль recent-аномалії (від найновішої): перевищення (`+7.2 % over threshold` / `unknown`), persistence (`confirmed` / `unknown: <detail>`), data quality (`clear` / `affected (N issues)` / `unknown`), same-snapshot anomalies з позначкою `causality not established`. Наприкінці підсумок: скільки з N оцінок мають `unknown` persistence/quality і що це означає (стійкість не підтверджена). Severity не присвоюється: evaluator її не дає. | 3-й у черзі |
| 5 | PSI | **Один раз**, з `pressure_observations`: 6 рядків `cpu some: avg10 0.12 / avg60 0.30 / avg300 0.25 %` (cpu/memory/io × some/full), відсутнє → `n/a`. Позначка: `sampled at <ts> (now); NOT temporally linked to the anomalies above`. Якщо PSI немає — коротко причини з `pressure_issues`. | ні (≤ ~8 рядків) |
| 6 | Data issues | Підрахунок за типами: `large_time_gap ×1, stale_data ×1`; окремо temporal errors і PSI issues за типами. Без `detail`-рядків. | 2-й у черзі |
| 7 | Historical | Один рядок: `Outside the window (analyzed <from>..<to>): 4 threshold anomalies, 1 persistent series, metrics: cpu_usage, last at <ts>` (+ unplaced, якщо > 0). | 1-й у черзі |
| 8 | Unknown | Детермінований перелік того, чого моніторинг **не знає**: поточний стан (якщо не fresh); відсутні значення current; persistence/quality `unknown` по N оцінках; affected services/users (не збираються); часовий зв'язок PSI з аномаліями; причина аномалій (моніторинг її не встановлює); недоступність моніторингу, якщо результату немає. | ніколи |

### 2.2 Алгоритм обрізання

1. Кожен блок рендериться як заголовок + список рядків-елементів.
2. Ліміт 10 у блоці 3 застосовується завжди (не залежить від бюджету), решта позначається `(N items omitted)`.
3. Поки загальна довжина `> max_chars`: з блоку 7, потім 6, потім 4, потім 3 видаляється **останній** (найстаріший) елемент, і маркер `(N items omitted)` оновлюється. До наступного блоку переходимо тільки тоді, коли попередній вже порожній (лишаються заголовок і маркер).
4. Якщо після цього текст все одно довший (можливо тільки при малому `max_chars`), PSI стискається до одного рядка `PSI omitted (N values)`, а потім блок 2 — до `Current values omitted`. Блоки 1 і 8 не чіпаються. Мінімум `max_chars` підібраний так, щоб 1 + 8 + маркери гарантовано вміщувалися. Тест це перевіряє на найгіршому вході.
5. Довгі рядкові поля з входу (timestamp, detail, назви метрик) обрізаються до 120 символів, щоб один зіпсований запис не з'їв бюджет.

## 3. Зміни в `app/agent/core.py`

- Замість `_compact_json(monitoring_result)`:
  ```python
  "Monitoring context (deterministic summary from JULI monitoring):\n"
  + build_monitoring_context(monitoring_result, get_monitoring_context_max_chars())
  ```
  Ліміт читається в момент виклику, тому зміна змінної оточення діє одразу.
- Якщо `JULI_MONITORING_CONTEXT_MAX_CHARS` невалідна, `ValueError` перехоплюється, використовується значення за замовчуванням 4000 і пишеться warning. Чат не повинен падати через конфіг.
- Гілка «Monitoring unavailable» лишається як є.
- `_compact_json` і `_json_safe` стануть невикористаними — **видаляю** (інших викликів у коді немає, перевірено `grep`).
- У `DEFAULT_SYSTEM_PROMPT` замінюю «the supplied Monitoring Analysis» на «the supplied Monitoring context». Інше в промпті не змінюю.
- `/context` і збереження останнього контексту — це Завдання 5, тут не роблю.

## 4. Тести

**Новий `tests/test_context_builder.py`** (фабрика синтетичних результатів у форматі `run_monitoring_cycle`):
- fresh: перший рядок `Data freshness: fresh`, поточні значення з timestamp;
- stale, no_data, відсутній `freshness`, `result=None`: перший рядок має `CURRENT SYSTEM STATE IS UNKNOWN`;
- `None`/NaN/inf у current → `unknown`, ніколи `0`;
- **50 аномалій** + 50 профілів, кожен з повним `psi_context` (18 PSI): `len ≤ 4000`, є `(N items omitted)`, блоки 1 і 8 присутні;
- PSI рівно один раз: блок PSI зустрічається 1 раз, значення PSI-маркера не повторюються, хоч профілів 50;
- recent: найновіші першими, не більше 10, маркер для решти;
- порядок обрізання: при підібраному ліміті спершу зникає historical, а recent ще повний; при меншому — data issues, потім significance;
- блоки 1 і 8 присутні на мінімальному ліміті з найгіршим входом; `len ≤ max_chars`;
- валідація `max_chars` (`bool`, `< MIN`, не int → `ValueError`); `None` бере значення з `JULI_MONITORING_CONTEXT_MAX_CHARS`;
- детермінованість (двічі однаковий текст) і незмінність входу (`deepcopy` до і після);
- «у рази коротше»: на реалістичній фікстурі (4 historical аномалії, 4 профілі по 18 PSI, як у реальній базі) `len(context) * 4 < len(json.dumps(result))`.

**`tests/test_agent_core.py`:**
- оновити тест, що шукав `"metric":"psi_cpu"`: тепер перевіряє заголовок `Monitoring context` і наявність PSI-рядка (фікстуру PSI перевести на справжню назву метрики `psi_cpu_some_avg10` з `context`);
- NaN-тест: замість `json.loads` перевіряє, що NaN відображається як `unknown`;
- новий: контекст не довший за `JULI_MONITORING_CONTEXT_MAX_CHARS` (monkeypatch env); невалідна змінна не ламає чат;
- новий: у повідомленні немає `Current structured`.

**Ручна перевірка на копії реальної бази** (не тест, тільки читання копії в scratchpad): довжина до (24 661) і після, перший рядок `stale`, PSI один раз. Цифри будуть у звіті.

## 5. Файли

| Файл | Дія |
|---|---|
| `app/agent/context_builder.py` | новий |
| `app/agent/core.py` | використати builder, оновити заголовок і одне слово в промпті, прибрати `_compact_json`/`_json_safe` |
| `tests/test_context_builder.py` | новий |
| `tests/test_agent_core.py` | оновити 2 тести, додати 2–3 |

**Не змінюються:** `significance_evaluator.py`, `analysis_service.py`, схема БД, `main.py` (`analyze --context` — Завдання 5), `dashboard.py`.

## 6. Питання для погодження

1. **Мінімальний `max_chars` = 1000** (менше дає `ValueError`). Згодні, чи краще без нижньої межі, але тоді при дуже малому ліміті блоки 1+8 можуть перевищити ліміт?
2. **Мова контексту — англійська.** Згодні?
3. **Видалити `_compact_json`/`_json_safe`** з `core.py`, чи залишити (наприклад, для майбутнього debug-виводу)?
4. Невалідна `JULI_MONITORING_CONTEXT_MAX_CHARS` в `AgentCore` → fallback на 4000 з warning (а не помилка в чаті). Згодні?
