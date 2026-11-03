# Завдання 3 — план: Current / Recent / Historical у моніторингу

*Стан: **чернетка, чекає на «ок»**. Код не змінено, створено лише цей файл.*

## 1. Поточна реалізація

`run_monitoring_cycle(limit=1000, min_samples=3, max_gap_seconds=15.0)` в `app/monitoring/analysis_service.py`:

1. Валідує параметри, потім `load_recent_snapshots(limit)` (останні 1000 рядків за `id`, хронологічно). **Часового фільтра немає.**
2. Будує `Observation` по кожному snapshot, збирає `data_issues` і `temporal_errors` (хронологія, `large_time_gap`, дублікати).
3. `detect_threshold_anomalies` → `detect_persistent_anomalies` → `evaluate_significance`. PSI береться з поточного `get_system_pressure()`.
4. Повертає словник із ключами: `status`, `snapshots_analyzed`, `threshold_anomalies(_found)`, `persistent_anomalies(_found)`, `data_issues`, `temporal_errors`, `pressure_observations`, `pressure_issues`, `significance_assessments`.
5. `status = "partial"` лише якщо є `temporal_errors`.
6. `analyze_system_history()` — обгортка, додає `anomalies` / `anomalies_found`.

**Наслідок:** свіжість нікуди не потрапляє. На копії реальної бази (останній snapshot `2026-10-31T14:23:12Z`) результат — `status: success`, 4 threshold-аномалії CPU, 1 стійка серія, і все це виглядає «поточним».

**Хто викликає:** `AgentCore` (через `run_monitoring_cycle` за замовчуванням), `main.py analyze`, тести. Зміни в `AgentCore` не потрібні: нові ключі просто додаються до JSON. До Завдання 4 вони збільшать контекст LLM, бо `recent` дублює об'єкти з верхнього рівня. Це свідомий тимчасовий компроміс.

**Важливий зв'язок з `significance_evaluator`.** Його не змінюємо. Але якщо додати issue `stale_data` без `timestamp` у `data_issues` *до* виклику `evaluate_significance`, evaluator сприйме його як «неприв'язану» проблему якості, і всі оцінки стануть `quality: unknown`, `persistence: unknown`. Тому **issue свіжості додається до результату після виклику evaluator**, а профілі лишаються бітово тими самими.

## 2. Пропоновані зміни (лише `analysis_service.py`, плюс тести)

### 2.1 Сигнатура
```python
run_monitoring_cycle(limit=1000, min_samples=3, max_gap_seconds=15.0,
                     recent_window_minutes=None, now=None)
analyze_system_history(..., recent_window_minutes=None, now=None)   # просто пересилає
```
- `recent_window_minutes=None` → `config.get_recent_window_minutes()` (15). Значення має бути скінченним додатним числом (не `bool`).
- `now=None` → `datetime.now(timezone.utc)`. Наївний `datetime` → `ValueError`; aware-час приводиться до UTC.
- Валідація нових параметрів іде **до** завантаження snapshots (як і наявних).

### 2.2 Нові ключі (поруч зі старими, старі не змінюються)

**`freshness`**
```
{ "latest_snapshot_timestamp": str | None,
  "latest_snapshot_age_seconds": float | None,
  "stale_after_seconds": 3 × config.get_collector_interval_seconds(),
  "state": "fresh" | "stale" | "no_data" }
```
- «Найновіший» snapshot — останній за порядком збору (`id`) з придатним для розбору timestamp. Не `max(timestamp)`: хронологію у проєкті свідомо не пересортовують.
- `fresh`, якщо `age <= stale_after_seconds` (межа включно); інакше `stale`.
- `no_data`: snapshots немає зовсім, **або** жоден не має придатного timestamp (вік визначити неможливо). Деталь пояснює data issue.
- Snapshot «з майбутнього» (`timestamp > now`): вік лишається від'ємним, не обнуляється, `state = "stale"` і додається issue `snapshot_in_future`, бо годинникові не можна довіряти.

**`current`** — `{"timestamp", "cpu_usage", "memory_usage", "disk_usage"}` з найновішого snapshot. Кожне значення проходить `parse_usage_percent`; непридатне стає `None`, не нуль. `None` цілком, якщо даних немає. Для `stale` `current` усе одно повертається (з timestamp), щоб Context Builder міг сказати «стан станом на …».

**`recent`**
```
{ "window_minutes", "window_start", "threshold_anomalies", "threshold_anomalies_found",
  "persistent_anomalies", "persistent_anomalies_found", "significance_assessments" }
```
Вікно — `[now − window, ∞)`: тільки нижня межа, щоб елемент із «майбутнім» часом не зникав мовчки.
- threshold-аномалія → `recent`, якщо її timestamp `>= window_start`;
- стійка серія → `recent`, якщо вона **перетинає** вікно (`end >= window_start`);
- significance assessment → за timestamp своєї аномалії (`assessment["anomaly"]["timestamp"]`).
Елементи мають ті самі об'єкти, що й на верхньому рівні (без перерахунку evaluator).

**`historical`** — лише зведення елементів поза вікном:
```
{ "threshold_anomalies_found", "persistent_anomalies_found",
  "last_anomaly_timestamp", "metrics": [відсортований перелік],
  "unplaced_threshold_anomalies", "unplaced_persistent_series" }
```
`last_anomaly_timestamp` — найпізніший timestamp серед historical-аномалій (`None`, якщо їх немає). Елементи, чий timestamp розібрати неможливо, не виносяться ні в `recent`, ні в `historical`, а лише рахуються в `unplaced_*` (нічого не вигадується). Охоплює **тільки завантажені `limit` snapshots**, а не всю базу.

### 2.3 `status` і data issue
- `status = "partial"`, якщо є `temporal_errors` **або** `freshness.state != "fresh"`.
- Після виклику evaluator до **поверненого** `data_issues` додається запис (значення рядкові, як в інших issues): `{"issue": "stale_data" | "no_data" | "snapshot_in_future", "latest_snapshot_timestamp", "age_seconds", "stale_after_seconds"}`. Він потрапляє в `_monitoring_issues` AgentCore і в підпис UI «Some Monitoring data is incomplete».
- `stale_after_seconds` береться з налаштованого інтервалу (`JULI_COLLECTOR_INTERVAL_SECONDS`, 10 с), а не з фактичного інтервалу запущеного Collector. Якщо Collector запущено з іншим `--interval`, межу треба змінити тією ж змінною.

### 2.4 `main.py analyze` (мінімально)
`format_analysis_summary` додає рядки: `Data freshness: stale (latest <ts>, age <N> s)`, `Recent: <n> threshold, <n> series`, `Historical: <n> threshold, <n> series`. Решта рядків не змінюється.

## 3. Тести (`tests/test_monitoring_freshness.py`, новий, з фіксованим `now`)

| Сценарій | Очікування |
|---|---|
| fresh | вік ≤ 30 с, `state=fresh`, `status=success`, `current` збігається з останнім snapshot |
| межа: вік рівно 30 с / 30.001 с | `fresh` / `stale` |
| stale | `state=stale`, `status=partial`, є data issue `stale_data`, `current` з timestamp |
| no_data (порожня БД) | `state=no_data`, `current=None`, `age=None`, `status=partial`, issue `no_data` |
| жоден snapshot без валідного timestamp | `no_data`, не виняток |
| snapshot із майбутнього | `stale` + `snapshot_in_future`, вік від'ємний |
| аномалія у вікні / поза вікном | перша в `recent`, друга лише в `historical` (лічильники, `last_anomaly_timestamp`, `metrics`) |
| серія, що перетинає межу вікна | потрапляє в `recent` |
| значення `current` непридатне (`None`/поза 0–100) | `None`, не нуль |
| невалідні `recent_window_minutes` / `now` (0, −1, `nan`, `True`, наївний datetime) | `ValueError` до завантаження даних |
| `recent_window_minutes` береться з `JULI_RECENT_WINDOW_MINUTES`; `stale_after` з `JULI_COLLECTOR_INTERVAL_SECONDS` | перевіряється через `monkeypatch.setenv` |
| старі ключі на місці з тими самими значеннями | порівняння зі списком ключів Завдання 3 |
| **significance не змінився** | профілі при stale і при fresh ідентичні (`stale_data` не потрапляє в evaluator) |
| `analyze_system_history` пересилає нові параметри й зберігає `anomalies` | |
| `main.py analyze` друкує freshness / recent / historical | у `tests/test_main.py` |

**Оновлення наявних тестів.** Вони використовують фіксовані дати `2026-10-11`, тож із реальним `now` стали б `stale`, а їх `assertEqual(result["status"], "success")` — хибними. Це свідомий наслідок вимоги «не fresh → partial». У п'яти тестах (`test_monitoring_integration.py` рядки ~96, 117, 137, 157, 454) додам `now=` у межах свіжості відносно даних. `test_collector_integration.py` пише snapshots у реальному часі, тож лишається як є (3 snapshots і аналіз за мілісекунди, межа 30 с). Інші тести статус не перевіряють.

## 4. Перевірка на копії реальної бази (не на оригіналі)

Копія вже зроблена в scratchpad з `data/juli.db.backup-20261103`; оригінал `data/juli.db` я не відкриваю. Сьогоднішній прогін старого коду на копії дав:
- останній snapshot `2026-10-31T14:23:12Z`, 1000 проаналізованих snapshots;
- **4 threshold-аномалії CPU** (`14:17:35`, `14:19:23`, `14:19:29`, `14:19:35`), **1 стійка серія** (`14:19:23–14:19:35`), 1 issue `large_time_gap`.

Очікування після змін: `freshness.state = stale` (вік ≈ 3 доби), `recent` = 0 threshold / 0 серій, `historical` = **4 threshold-аномалії** (саме їх мав на увазі «4» у документі) і **1 серія**, `last_anomaly_timestamp = 2026-10-31T14:19:35`, `metrics = ["cpu_usage"]`, `status = partial`, `current` = значення з `14:23:12`.

## 5. Що не змінюється
- `significance_evaluator.py`, `anomaly_detector.py`, `temporal_analyzer.py`, `data_quality.py`, `history_reader.py`, схема БД, `AgentCore`, `data/juli.db`.
- Усі наявні ключі результату та їхні значення (за винятком `status`, який за вимогою стає `partial` при `stale` / `no_data`).
- Нових залежностей немає. Нічого не комітиться без вашого дозволу.

## 6. Ризики
- Зміна `status` для даних, що не fresh, — єдина навмисна поведінкова зміна наявного ключа. Споживач, який перевіряє лише `status == "success"`, тепер побачить `partial` для застарілих даних, і це відповідає меті.
- До Завдання 4 JSON для LLM трохи зростає (дублювання в `recent`); на реальній базі `recent` порожній.
- `historical` охоплює лише `limit=1000` останніх snapshots (≈ 2,8 год при 10 с). Довшу історію не аналізуємо, і це треба чітко казати в Context Builder.

## 7. Питання до вас
1. **Snapshot з майбутнього** позначати `stale` + issue `snapshot_in_future` (мій варіант) чи окремо не обробляти?
2. **Серія, що перетинає межу вікна**, потрапляє в `recent` (перетин) чи лише ті, що повністю в вікні?
3. **Issue свіжості додається після `evaluate_significance`**, щоб профілі не змінювалися, — приймаєте?
4. **Оновлення п'яти наявних тестів** (`now=` замість реального часу) — приймаєте?
