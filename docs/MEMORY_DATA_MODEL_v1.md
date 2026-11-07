# JULI — Memory Data Model v1.0, погоджено 07.11.2026

Дата: 07.11.2026. Статус: **погоджено; реалізація — кроки M1–M7 (розділ 6)**. Це специфікація для реалізації. Розділ 9 уточнює розділи 1–4 за результатами аналізу коду і там, де вони розходяться, має пріоритет.

## 0. Принципи

1. **Пам'ять ≠ snapshots.** Пам'ять не копіює сирі дані, а посилається на них (snapshot, ToolResult). Snapshots лишаються як є.
2. **Пам'ять пропонує, Tool Layer перевіряє.** Знання з Long-Term Memory потрапляє в новий інцидент лише як *гіпотеза* (`origin = memory`), яку треба заново перевірити інструментами на поточній системі. Це і є «reconstruction» у JULI.
3. **Статус ставить код, а не LLM.** LLM може запропонувати гіпотезу чи зв'язок, але `verified` / `refuted` встановлюється детермінованими правилами за доказами або підтвердженням користувача. (Обґрунтування — Етап 0: модель 4B ненадійно виконує правила промпту.)
4. **Кожне твердження має походження.** Хто створив (rule / llm / user / memory), на якому доказі ґрунтується.
5. **Схожість ≠ причинність.** Зв'язки поділено на чотири класи: associative, semantic, evidential, causal. Associative ніколи не є доказом; що доказ підтримує чи спростовує — це evidential (розд. 9, C15).
6. **Історія не губиться.** Кожна зміна статусу пишеться в журнал подій.

## 1. Погоджені рішення (змінити, якщо не так)

| # | Рішення |
|---|---|
| D1 | Incident відкриває **користувач**. JULI може **запропонувати** відкрити (напр., після підтвердженої аномалії від Significance Evaluator), але не відкриває сама. |
| D2 | Наявний `app/models/observation.py` **не дублюється**. Запис у пам'яті посилається на snapshot / ToolResult через `source_type` + `source_ref`. |
| D3 | Hypothesis: `proposed → testing → supported / refuted / inconclusive` + поле `origin` (`llm` / `memory` / `user`). |
| D4 | ToolResult, які використано як доказ, **зберігаються** в окремій таблиці (зараз вони не персистуються). Без цього немає провенансу доказів. |
| D5 | v1 без embeddings, векторного пошуку та графової БД. Граф — це таблиця зв'язків у SQLite. |
| D6 | Два режими чату. (1) **General Chat** — буденні питання; має лише Conversation History, у Contextual Memory нічого не пише; може запропонувати відкрити інцидент. (2) **Incident Chat** — розмова всередині інциденту; усі спостереження, гіпотези, перевірки, дії Tool Layer і їхні результати пишуться в Contextual Memory. Реалізація — M5. |

## 2. Сутності й таблиці SQLite

Схема додається як міграція `user_version 1 → 2` через наявний `ensure_schema()`. ID — TEXT з префіксом (`inc_…`, `obs_…`, `hyp_…`), час — ISO 8601 UTC.

### 2.1. `incidents` — Contextual Memory, корінь розслідування

| Поле | Тип | Опис |
|---|---|---|
| id | TEXT PK | `inc_<uuid>` |
| title | TEXT | короткий опис |
| symptom_text | TEXT | симптом словами користувача |
| status | TEXT | `open` / `investigating` / `resolved` / `unresolved` |
| opened_by | TEXT | `user` / `juli_suggested` (з підтвердженням користувача) |
| host_context | JSON | hostname, OS, kernel, версія JULI — для області чинності знань |
| root_cause_hypothesis_id | TEXT FK nullable | гіпотеза, визнана причиною |
| resolution_summary | TEXT nullable | |
| created_at / updated_at / closed_at | TEXT | |

Переходи: `open → investigating → resolved | unresolved`; повторне відкриття `resolved/unresolved → investigating` дозволене і пишеться в журнал.

### 2.2. `observations` — що побачили

| Поле | Тип | Опис |
|---|---|---|
| id | TEXT PK | |
| incident_id | TEXT FK | |
| kind | TEXT | `symptom` / `metric` / `state` / `log` / `user_report` |
| statement | TEXT | нормалізований короткий текст («disk usage 89.3%») |
| resource, metric | TEXT nullable | `disk`, `usage_percent` |
| value, unit | REAL/TEXT nullable | |
| observed_at | TEXT | коли виміряно (не коли записано) |
| freshness | TEXT | `fresh` / `stale` / `unknown` |
| source_type | TEXT | `snapshot` / `tool_result` / `psi` / `user` |
| source_ref | TEXT nullable | id snapshot або tool_result |

### 2.3. `hypotheses` — можливі пояснення

| Поле | Тип | Опис |
|---|---|---|
| id | TEXT PK | |
| incident_id | TEXT FK | |
| statement | TEXT | «Disk fills up due to growing journal logs» |
| origin | TEXT | `llm` / `memory` / `user` |
| origin_knowledge_id | TEXT FK nullable | якщо `origin = memory` |
| status | TEXT | `proposed` / `testing` / `supported` / `refuted` / `inconclusive` |
| status_reason | TEXT nullable | чому такий статус (посилання на правило / evidence) |
| created_at / updated_at | TEXT | |

Числової «впевненості» від LLM немає навмисно: вона неперевірювана. Сила підтвердження виводиться з доказів.

### 2.4. `diagnostic_actions` — перевірки й дії

| Поле | Тип | Опис |
|---|---|---|
| id | TEXT PK | |
| incident_id | TEXT FK | |
| hypothesis_id | TEXT FK nullable | яку гіпотезу перевіряє |
| kind | TEXT | `read_check` / `action` |
| tool_name | TEXT | з Tool Registry |
| parameters | JSON | |
| initiator | TEXT | `llm` / `user` / `memory_reverification` |
| risk_level | TEXT nullable | заповнить Risk Analyzer пізніше |
| approval | TEXT | `not_required` / `pending` / `approved` / `rejected` |
| status | TEXT | `planned` / `running` / `succeeded` / `failed` / `skipped` |
| tool_result_id | TEXT FK nullable | |
| started_at / finished_at | TEXT | |

`succeeded` означає «інструмент відпрацював», а не «проблему розв'язано».

### 2.5. `tool_results` — збережені результати інструментів (D4)

| Поле | Тип | Опис |
|---|---|---|
| id | TEXT PK | |
| tool_name, status | TEXT | status з ToolResult (`success` / `partial` / `error`) |
| parameters, payload | JSON | payload обрізається до ліміту, санітизується |
| created_at | TEXT | |

### 2.6. `evidence` — доказ

| Поле | Тип | Опис |
|---|---|---|
| id | TEXT PK | |
| incident_id | TEXT FK | |
| summary | TEXT | що саме показує доказ |
| source_type, source_ref | TEXT | `tool_result` / `observation` / `user` |
| quality | TEXT | `fresh` / `stale` / `partial` |
| assessed_by | TEXT | `rule` / `llm` / `user` |
| created_at | TEXT | |

Що доказ **підтримує чи спростовує** — записується зв'язком у `memory_relations` (supports / contradicts), а не тут.

### 2.7. `memory_relations` — граф

| Поле | Тип | Опис |
|---|---|---|
| id | TEXT PK | |
| src_type, src_id | TEXT | напр. `evidence`, `ev_…` |
| relation | TEXT | `supports`, `contradicts`, `causes`, `verified_by`, `resolved_by`, `depends_on`, `derived_from`, `similar_to` |
| relation_class | TEXT | `associative` / `semantic` / `evidential` / `causal` (`supports`, `contradicts` — завжди `evidential`, C15) |
| dst_type, dst_id | TEXT | |
| status | TEXT | `asserted` / `verified` / `refuted` |
| basis_evidence_id | TEXT FK nullable | на якому доказі ґрунтується |
| created_by | TEXT | `rule` / `llm` / `user` |
| created_at | TEXT | |

### 2.8. `knowledge` — Long-Term Memory

| Поле | Тип | Опис |
|---|---|---|
| id | TEXT PK | |
| kind | TEXT | `root_cause_pattern` / `procedure` / `dependency` / `failed_attempt` / `unverified_hypothesis` |
| statement | TEXT | |
| symptom_signature | JSON | ресурс, метрика, умови (`disk.usage_percent > 85`) — для retrieval |
| verification_procedure | JSON | список read-checks, які колись підтвердили знання — для повторної перевірки |
| status | TEXT | `verified` / `failed` / `unverified` / `deprecated` |
| scope | JSON | host / OS / kernel / версії пакетів, де знання перевірене |
| source_incident_ids | JSON | звідки знання взялося |
| times_confirmed, times_refuted | INTEGER | |
| last_confirmed_at | TEXT nullable | |
| superseded_by | TEXT FK nullable | |
| created_at / updated_at | TEXT | |

### 2.9. `memory_events` — журнал змін

`id, entity_type, entity_id, event (created / status_changed / linked / consolidated / reopened), old_value, new_value, reason, actor (rule / llm / user), at`.

## 3. Інваріанти (перевіряються кодом і тестами)

1. `relation_class = causal` зі `status = verified` вимагає `basis_evidence_id`.
2. `similar_to` завжди `associative` і ніколи не робить гіпотезу `supported`.
3. LLM не може встановити `verified` / `supported` / `refuted` напряму — лише запропонувати; статус ставить правило або користувач.
4. Гіпотеза `supported` має ≥ 1 зв'язок `supports` від evidence з `source_type = tool_result` (або підтвердження користувача) і жодного неспростованого `contradicts`.
5. Гіпотеза з `origin = memory` не може стати `supported` без нової перевірки в поточному інциденті.
6. Knowledge `failed` ніколи не повертається retrieval як рекомендоване рішення — лише як попередження «це вже не спрацювало». Knowledge `deprecated` retrieval не повертає взагалі — ні як рекомендацію, ні як попередження (розд. 9, C4).
7. Кожна зміна статусу → запис у `memory_events`.

## 4. Життєвий цикл

```
Incident (open)
  → Observations (з snapshot / ToolResult / слів користувача)
  → Hypotheses (origin: llm | memory | user; status: proposed)
  → DiagnosticActions (read_check через Tool Layer) → tool_results
  → Evidence → memory_relations (supports / contradicts)
  → правила оновлюють статуси гіпотез
Incident → resolved | unresolved
  → Evaluation: supported / refuted / inconclusive
  → Knowledge Extraction (кандидати)
  → Consolidation:
       supported + tool evidence  → knowledge verified (або times_confirmed++)
       refuted                    → knowledge failed_attempt
       inconclusive               → knowledge unverified
       нове знання суперечить старому → старе deprecated / superseded_by
```

**Retrieval (пізніше, v1 — без embeddings):** збіг `symptom_signature` з поточними observations + фільтр за `scope` → знання стають гіпотезами `origin = memory, status = proposed` разом зі своєю `verification_procedure` → JULI заново виконує ці перевірки.

## 5. Поза межами v1

Embeddings і векторний пошук; графова БД; автоматичне відкриття інцидентів; Risk Analyzer і Action Tools (поле `risk_level` лише зарезервоване); LLM-оцінка «сили» доказу.

## 6. Порядок реалізації

| Крок | Що | LLM потрібна? |
|---|---|---|
| M1 | Міграція схеми v2, dataclasses-моделі, repository-шар (CRUD), інваріанти, тести | ні |
| M2 | Lifecycle інциденту: `main.py incident open / list / show / close`, журнал подій | ні |
| M3 | Персистування ToolResult; DiagnosticAction → tool_result → Evidence | ні |
| M4 | Правила статусів гіпотез (інваріанти 3–5) | ні |
| M5 | Інтеграція з AgentCore: чат у межах інциденту; LLM пропонує гіпотези/перевірки, код валідує й записує | так |
| M6 | Evaluation + Consolidation → knowledge | ні (правила) |
| M7 | Retrieval за сигнатурою + повторна перевірка (reconstruction) | частково |

M1–M4 і M6 детерміновані й повністю тестуються без моделі. Після M7 можливі експерименти: No Memory / Conversation History / Similarity Retrieval / Evidence-Guided Memory.

## 7. Зв'язок зі статтею та дипломом

| Розділ статті | Що бере з цього документа | Статус у статті |
|---|---|---|
| Section 4. Proposed Memory Model | Принципи (розд. 0), дворівнева модель, «пам'ять пропонує — Tool Layer перевіряє» | proposed design |
| Section 5. Memory Organization | Сутності (розд. 2), життєвий цикл і консолідація (розд. 4) | proposed design |
| Section 6. Causal and Semantic Relations | `memory_relations`, чотири класи зв'язків, інваріанти 1–5 | proposed design |
| Section 7. Preliminary Evaluation Plan | 4 режими (No Memory / Conversation History / Similarity Retrieval / Evidence-Guided), сценарії «схожі симптоми — різні причини» | план, без результатів |
| Section 8. Discussion | Обмеження 4B-моделі з Етапу 0 як аргумент, чому статуси ставить код | спостереження, не експеримент |

Стаття — огляд + запропонований дизайн (не подавати як реалізоване). Диплом — реалізація M1–M7 + експерименти + результати.

## 8. Відкриті питання

1. ~~Мова поля `statement`~~ → **англійська** (погоджено 07.11.2026).
2. Ліміт розміру `tool_results.payload` і що санітизувати (шляхи, ім'я користувача).
3. Чи потрібен окремий екран Incidents у dashboard уже на M2, чи достатньо CLI.

## 9. Уточнення після аналізу коду

Погоджено 07.11.2026 (план `docs/memory_m1_plan.md`, розділ 2). Там, де цей розділ розходиться з розділами 1–4, діє він.

| # | Рішення |
|---|---|
| C1 | `tool_results` має ще колонку `error` (JSON, nullable) — поле `error` з `ToolResult` (`code`, `message`, `details`). `payload` = `ToolResult.data`. |
| C2 | Таблиця `observations` — як у розд. 2.2. Dataclass називається `IncidentObservation` (`app/memory/models.py`), щоб не плутати з `app/models/observation.Observation` (D2). |
| C3 | Інваріанти 3–5 у M1 — **запобіжники**: repository відхиляє недозволений перехід. У M4 — **правила**, що самі вирішують, коли змінити статус. |
| C4 | Інваріант 6 у M1 — два методи читання: рекомендоване знання (`verified`, `unverified`) і попередження (лише `failed`). **`deprecated` retrieval пропускає повністю**: його замінило новіше знання (`superseded_by`), тож воно не повертається ні як рекомендація, ні як попередження (рішення 07.11.2026). Retrieval у M7 мусить користуватися цими методами. |
| C5 | Перед міграцією наявної бази (у ній вже є таблиці) `ensure_schema()` автоматично робить резервну копію `<db>.backup-YYYYMMDD-v<стара версія>`. Наявну копію не перезаписує. **Якщо копія не вдалась, міграція не виконується.** Порожня нова база мігрує без копії. |
| C6 | `PRAGMA foreign_keys = ON` на кожному з'єднанні (`get_connection()`). |
| C7 | `actor` у журналі — лише `rule` / `llm` / `user`. Гіпотезу з `origin = memory` створює `rule` (детермінований retrieval). Інцидент з `opened_by = juli_suggested` відкриває `user` (D1). |
| C8 | `llm` лише **створює** записи (гіпотеза `proposed`, зв'язок `asserted`, дія `planned`, observation, evidence) і переводить гіпотезу `proposed → testing`. Інші зміни статусів — `rule` або `user`. Відкриття інциденту і `approval` (`approved` / `rejected`) — **тільки `user`**. Knowledge створюють і змінюють лише `rule` / `user`. |
| C9 | Підтвердження користувача (інв. 4) — подія в `memory_events` з `actor = user` і обов'язковим непорожнім `reason`: це зміна статусу гіпотези на `supported`, яку робить користувач. |
| C10 | «Нова перевірка» (інв. 5) — evidence з `source_type = tool_result`, де tool_result отримано дією (`diagnostic_actions`) **цього** інциденту **після** створення гіпотези. Підтвердження лише користувачем для `origin = memory` не зараховується. |
| C11 | Повернення гіпотези з `supported` / `refuted` / `inconclusive` у `testing` — тільки (а) `rule` за наявності нового доказу: зв'язку `supports` / `contradicts` з гіпотезою, створеного після її останньої зміни статусу; або (б) `user`. Завжди із записом у журнал. |
| C12 | `statement` (observations, hypotheses, knowledge) — англійською. Мінімальний запобіжник у коді: кирилиця в `statement` відхиляється. Інші текстові поля (`symptom_text`, `summary`, `reason`) — без обмеження мови. |
| C13 | `payload` понад ліміт (M1: 64 KiB серіалізованого JSON) обрізається: зберігається `{"truncated": true, "original_size_bytes": N, "preview": "<початок JSON-тексту>"}`. Санітизація — у M3 (відкрите питання 8.2). |
| C14 | `root_cause_hypothesis_id` — лише гіпотеза **цього** інциденту зі статусом `supported`. |
| C15 | *(додано й погоджено 07.11.2026, до коміту M1)* Четвертий клас зв'язків **`evidential`**: `relation_class` ∈ `associative` / `semantic` / `evidential` / `causal`. Зв'язки `supports` і `contradicts` — **завжди** `evidential` (перевіряється кодом і `CHECK` у схемі). Отже, семантичний чи асоціативний зв'язок не може підтвердити або спростувати гіпотезу (інв. 2, 4). Для інших зв'язків клас не обмежено, крім `similar_to` → `associative` (інв. 2). |

Також у M1:

- Переходи, не описані в розд. 2: `diagnostic_actions.status` `planned → running | skipped`, `running → succeeded | failed`; `approval` `pending → approved | rejected` (`not_required` не змінюється); `memory_relations` `asserted → verified | refuted`; `knowledge` `unverified → verified | failed`, `verified ↔ failed`, будь-який → `deprecated` (кінцевий).
- ID: `inc_`, `obs_`, `hyp_`, `act_`, `tr_`, `ev_`, `rel_`, `kn_`, `evt_` + uuid4 hex.
- Журнал пише також події `created` (створення запису) і `linked` (створення зв'язку).
