# Завдання 5 — план: налаштування Ollama (Qwen, think, num_ctx), usage і термінальний чат

*Стан: **реалізовано** (03.11.2026). План погоджено 03.11.2026; звіт — у кінці файлу.*

## 1. Поточна реалізація

- `app/llm/config.py` — `OllamaConfig(host, model="llama3.2", timeout_seconds=120)`, `from_environment()` читає `JULI_OLLAMA_HOST`, `JULI_OLLAMA_MODEL`, `JULI_OLLAMA_TIMEOUT_SECONDS`.
- `app/llm/ollama.py` — `POST /api/chat` з тілом `{"model", "messages", "stream": false}`. Без `options` і `think`. Читає тільки `message.content`; поля `thinking` і лічильники токенів ігноруються.
- `app/llm/interface.py` — `LLMResult(status, content, model, error)`, без `usage`.
- `main.py` — `init-db`, `collect`, `analyze`. Команд `chat` і `analyze --context` немає.
- `tests/test_ollama_provider.py:36` перевіряє тіло запиту точною рівністю `{"model": "llama3.2", "messages": ..., "stream": False}` — тест доведеться оновити.
- Локально встановлена лише `qwen3.5:4b` (`ollama list`); `llama3.2` немає, тож чат зараз падає з `MODEL_NOT_FOUND`.

## 2. Що перевірено на реальній моделі (03.11.2026, тільки читання)

Ollama 0.40.13, `qwen3.5:4b` (4.2B, Q4_K_M), RTX 4050 Laptop 6 ГБ, RAM 15 ГБ. Скрипти й сирі відповіді — у scratchpad, у репозиторій не додаються.

### 2.1 `ollama show qwen3.5:4b`

| Параметр | Значення |
|---|---|
| context length (максимум моделі) | **262 144** |
| capabilities | completion, vision, tools, **thinking** (levels: false, true; **default: true**) |
| parser / renderer | `qwen3.5` (Ollama сама відокремлює міркування) |
| параметри Modelfile | temperature 1, top_k 20, top_p 0.95, presence_penalty 1.5 |

### 2.2 Як повертаються міркування (`/api/chat`, `stream: false`)

| `think` у запиті | ключі `message` | `content` | `thinking` | eval_count |
|---|---|---|---|---|
| не передано | content, role, **thinking** | `391` | 848 символів | 295 |
| `false` | content, role | `391` | відсутнє | 4 |
| `true` | content, role, **thinking** | `391` | 848 символів | 295 |

Висновки:
- Міркування приходять **окремим полем `message.thinking`**, `content` чистий: тегів `<think>` немає ні в простому, ні в реальному запиті JULI.
- **Якщо `think` не передати, модель думає** (default true). Тому `think` треба передавати **завжди явно**.
- `eval_count` включає токени міркувань.

### 2.3 Реальний запит JULI (копія `data/juli.db`, стан `stale`)

Повідомлення зібрані справжнім `AgentCore` (системний промпт + Context Builder); питання: «Що зараз відбувається із системою? Чи є проблеми?»; `temperature 0.2`, `seed 42`, `num_ctx 16384`.

| | Символів контексту | `prompt_eval_count` |
|---|---|---|
| Старий JSON (`_compact_json`, до Завдання 4) | 24 945 | **9 836** |
| Context Builder | 1 668 | **876** (у 11 разів менше) |

| `think` | Час | eval_count | thinking | Мова відповіді |
|---|---|---|---|---|
| `false` | **10.6 с** | 567 | — | українська |
| `true` | **46.1 с** | 2 453 | 6 644 символи (окремо) | українська |

Холодний старт (завантаження моделі) — ~60 с на перший запит.

**Українська:** на українське питання з англійським контекстом обидві відповіді українською, коректно кажуть, що поточний стан невідомий (дані застарілі на 3.1 дня), і не вигадують причин. Помічені вади (не виправляються в цьому завданні, записую для Завдання 7):
- граматика: «зафіксовано жодних аномалій» (пропущено «не»), «минуле подія»;
- «PSI … у середньому за 10 хвилин» — `avg10` це 10 **секунд**; «Pressure Index» замість Pressure Stall Information;
- think=false: «прямих проблем не виявлено» — трохи надто впевнено при `stale`, хоча поруч сказано, що стан невідомий.

### 2.4 Вибір `num_ctx`

| `num_ctx` | Розмір у пам'яті | Розміщення | VRAM зайнято |
|---|---|---|---|
| 8 192 | 3.4 GB | 100 % GPU | 3 982 MiB |
| **16 384** | **3.6 GB** | **100 % GPU** | **4 164 MiB** |
| 32 768 | 4.1 GB | 100 % GPU | 4 692 MiB |
| 65 536 | 5.7 GB | 24 % CPU / 76 % GPU | 4 936 MiB |

Оцінка найгіршого промпту: системний промпт + контекст на ліміті 4 000 символів ≈ 1 500 токенів; історія `max_turns=12` × (~20 + ~570 токенів) ≈ 7 100; відповідь ≈ 600 (think=false) або ≈ 2 500 (think=true). Разом ≈ 9 200 / 11 100 токенів — **8 192 замало** (Ollama мовчки обрізає початок промпту, тобто саме системний промпт і контекст). **Пропоную 16 384**: запас ~40 %, модель повністю на GPU, лишається ~2 ГБ VRAM. Максимум моделі (262 144) на цій машині недосяжний без CPU-offload і не потрібен.

`num_ctx` має бути сталим між запитами: зміна змушує Ollama перезавантажити модель.

## 3. Зміни

### 3.1 `app/llm/config.py` — `OllamaConfig`

| Поле | Змінна | За замовчуванням | Валідація |
|---|---|---|---|
| `model` | `JULI_OLLAMA_MODEL` | **`qwen3.5:4b`** | непорожній рядок (як зараз) |
| `think` | `JULI_OLLAMA_THINK` | **`false`** | `bool`; з env: `true/false/1/0/yes/no` без урахування регістру, інше → `ValueError` з назвою змінної |
| `num_ctx` | `JULI_OLLAMA_NUM_CTX` | **16 384** | ціле (не `bool`), `>= 2048` |
| `temperature` | `JULI_OLLAMA_TEMPERATURE` | 0.2 | скінченне число, `0 <= t <= 2` |
| `seed` | `JULI_OLLAMA_SEED` | `None` | ціле або `None` (порожній рядок = `None`) |

`JULI_OLLAMA_MODEL` і далі перевизначає модель. Інші параметри Modelfile (top_k, top_p, presence_penalty) не чіпаю.

### 3.2 `app/llm/ollama.py`

- Тіло запиту: `{"model", "messages", "stream": false, "think": <bool>, "options": {"num_ctx", "temperature"[, "seed"]}}`. `think` передається **завжди**, навіть `false`.
- **Міркування ніколи не потрапляють у `content`:**
  - відповідь береться тільки з `message.content`; `message.thinking` не читається і не зберігається;
  - захист для моделей/версій без parser: якщо `content` починається з блоку `<think>…</think>`, блок вирізається (з Qwen на 0.40.13 не спостерігалось, але це гарантія незалежно від моделі);
  - якщо після цього `content` порожній (наприклад, модель витратила все на міркування) → `EMPTY_RESPONSE`; **без fallback на thinking**.
- Модель без підтримки thinking при `think=true`: Ollama повертає помилку (очікую текст «does not support thinking»; **не перевірено** — такої моделі локально немає). Мапиться в `THINKING_NOT_SUPPORTED` з порадою `JULI_OLLAMA_THINK=false`. Перевіряється мок-тестом.
- `usage` з відповіді: `prompt_eval_count`, `eval_count`, `total_duration` (нс), `done_reason`. Відсутні або некоректні поля → `None`, без помилки.
- Якщо `prompt_eval_count + eval_count >= num_ctx` або `done_reason == "length"` → `logger.warning` (контекст міг бути обрізаний).

### 3.3 `app/llm/interface.py`

- `LLMUsage(prompt_eval_count, eval_count, total_duration_ns, done_reason)` — усі `int | str | None`.
- `LLMResult.usage: LLMUsage | None = None`; `to_dict()` додає `"usage"`. Сумісно з наявними викликами.

### 3.4 `app/agent/core.py`

- Зберігати останні передані моделі повідомлення (`last_messages`, копія) — для `/context`.
- Додати `usage` у результат `handle_message`.
- Системний промпт не змінюю.

### 3.5 `main.py` (з плану `STAGE_0`, Завдання 5)

- `chat` — термінальний чат з `AgentCore`: `/clear` очищає історію, `/context` друкує останній контекст, переданий моделі, `/exit` (і Ctrl+D/Ctrl+C) — вихід. Після кожної відповіді один рядок: модель, `prompt/eval` токени, час. Помилки LLM друкуються без traceback.
- `analyze --context` — друкує те, що отримає LLM (Context Builder з поточним лімітом), без звернення до Ollama.

### 3.6 Документація

- `docs/STAGE_0_STABILIZATION.md:29`: «`llama3.2` за замовчуванням» → `qwen3.5:4b`, think вимкнено.
- `docs/task4_plan.md` не правлю (історичний документ).

## 4. Тести (Ollama замокана, реальних запитів у тестах немає)

**`tests/test_ollama_provider.py`:**
- тіло запиту містить `think: false` і `options` з `num_ctx`, `temperature`; `seed` тільки якщо задано;
- env: `JULI_OLLAMA_THINK` (`true`/`0`/`Yes`, невалідне → `ValueError`), `NUM_CTX`, `TEMPERATURE`, `SEED`; валідація меж і `bool`;
- модель за замовчуванням `qwen3.5:4b`;
- відповідь з `message.thinking` → `content` без міркувань, слова з thinking відсутні в `result.content` і `to_dict()`;
- `content` з `<think>…</think>` → блок вирізано; тільки `<think>` без тексту після → `EMPTY_RESPONSE`;
- помилка «does not support thinking» → `THINKING_NOT_SUPPORTED`;
- `usage` парситься; відсутній або з неправильними типами → `None` без помилки; warning при заповненому контексті.

**`tests/test_agent_core.py`:** `usage` у результаті; `last_messages` містить контекст.

**`tests/test_main.py`:** `analyze --context` друкує freshness першим рядком і не звертається до Ollama; `chat` з підставним `input` і фейковим провайдером: `/context`, `/clear`, `/exit`, помилка LLM без traceback.

**Ручна перевірка після реалізації:** `main.py chat` на копії бази з `think=false` і `think=true` — у виводі немає міркувань, `prompt_eval_count` ≈ 876.

## 5. Файли

| Файл | Дія |
|---|---|
| `app/llm/config.py` | модель, think, num_ctx, temperature, seed |
| `app/llm/ollama.py` | `think` + `options`, захист від міркувань у content, `usage`, нова помилка |
| `app/llm/interface.py` | `LLMUsage`, `LLMResult.usage` |
| `app/agent/core.py` | `last_messages`, `usage` у результаті |
| `main.py` | `chat`, `analyze --context` |
| `tests/test_ollama_provider.py`, `tests/test_agent_core.py`, `tests/test_main.py` | оновити/додати |
| `docs/STAGE_0_STABILIZATION.md` | рядок про модель |

**Не змінюються:** Context Builder, моніторинг, схема БД, `dashboard.py` (отримає нові налаштування автоматично через `OllamaConfig.from_environment()`).

## 6. Питання для погодження

1. **`num_ctx = 16 384`** (а не 8 192 з плану `STAGE_0`) — згодні?
2. **Міркування не зберігати взагалі** (навіть для debug). Альтернатива — зберігати лише довжину (`thinking_chars`) в `usage` для експериментів. Що обираєте?
3. **`chat` і `analyze --context`** робимо в цьому ж завданні (як у `STAGE_0`) чи винести окремо, щоб Завдання 5 лишилось тільки про Ollama?
4. Вада з `avg10` = «10 хвилин»: виправити окремо в Context Builder (підписати вікна як `10 s / 60 s / 300 s`) — зараз чи в Завданні 7?


## Звіт

### Рішення з погодження

1. `num_ctx = 16 384` — так.
2. Текст міркувань не зберігається; у `usage` є `thinking_chars` — довжина `message.thinking` або `None`.
3. `chat` і `analyze --context` — у цьому завданні.
4. Підписи PSI виправлено зараз: `PSI (Pressure Stall Information)` і `avg10 (10 s) / avg60 (60 s) / avg300 (300 s)`.

### Змінені файли

| Файл | Що зроблено |
|---|---|
| `app/llm/config.py` | модель `qwen3.5:4b`; `think` (`JULI_OLLAMA_THINK`, false), `num_ctx` (`JULI_OLLAMA_NUM_CTX`, 16384, ≥ 2048), `temperature` (`JULI_OLLAMA_TEMPERATURE`, 0.2, 0..2), `seed` (`JULI_OLLAMA_SEED`, порожнє = `None`); валідація; `options()`. Помилка env містить назву змінної. |
| `app/llm/interface.py` | `LLMUsage(prompt_eval_count, eval_count, total_duration_ns, done_reason, thinking_chars)`; `LLMResult.usage`; `to_dict()` містить `usage`. |
| `app/llm/ollama.py` | у запиті завжди `think` і `options`; відповідь — лише `message.content`, `<think>…</think>` на початку вирізається (також незакритий); порожній результат → `EMPTY_RESPONSE` без fallback на міркування; `usage` з відповіді (некоректні значення → `None`); warning, якщо `prompt + eval ≥ num_ctx` або `done_reason == "length"`; помилка «does not support thinking» → `THINKING_NOT_SUPPORTED`. |
| `app/agent/core.py` | `last_messages` (копія повідомлень, переданих моделі); `usage` у кожному результаті `handle_message` (`None`, якщо немає); `monitoring_context_message()` — спільний текст для `AgentCore` і `analyze --context`. |
| `app/agent/context_builder.py` | назва і підписи вікон PSI. |
| `main.py` | `chat` (`/context`, `/clear`, `/exit`/`/quit`, Ctrl+D/Ctrl+C; рядок з моделлю, токенами, часом і `thinking N chars (not shown)`); `analyze --context` (те, що отримує модель; кількість символів — у stderr). |
| `docs/STAGE_0_STABILIZATION.md` | рядок про модель за замовчуванням. |
| `tests/test_ollama_provider.py`, `tests/test_agent_core.py`, `tests/test_context_builder.py`, `tests/test_main.py` | оновлено й додано тести (див. нижче). |

### Відхилення від плану

1. `analyze --context` при невалідній `JULI_MONITORING_CONTEXT_MAX_CHARS` не завершується помилкою, а, як `AgentCore`, бере 4000 і пише warning — щоб команда показувала саме те, що отримає модель.
2. У результаті `AgentCore` ключ `usage` є завжди (у гілках без відповіді провайдера — `None`), щоб структура була однаковою.
3. Невідома команда в чаті (`/help` тощо) не надсилається моделі, а друкує список команд.
4. `from_environment()` додає до помилки валідації префікс `Invalid JULI_OLLAMA_* configuration`.

### Тести

- `.venv/bin/python -m pytest -q` → **347 passed, 47 subtests passed in 1.39s** (до завдання: 299 passed).
- `git diff --check` → без зауважень.
- Нове: значення за замовчуванням і env для `think`/`num_ctx`/`temperature`/`seed` та їх валідація; тіло запиту з `think` і `options`; `message.thinking` не потрапляє ні в `content`, ні в `to_dict()`, а `thinking_chars` рахується; вирізання `<think>`; `EMPTY_RESPONSE` без fallback; парсинг і некоректний `usage`; warning при заповненому контексті; `THINKING_NOT_SUPPORTED` (HTTP-помилка і поле `error`); `usage` і `last_messages` в `AgentCore`; `analyze --context` без звернення до Ollama; сценарії `chat` з підставним провайдером (відповідь, `/context`, `/clear`, невідома команда, помилка LLM без traceback, EOF, невалідний env).

### Ручна перевірка `main.py chat` (копія `data/juli.db`, `think=false`)

Запуск: `JULI_DB_PATH=<копія> JULI_OLLAMA_THINK=false main.py chat`, ввід з файлу: `/context`, два питання, `/context`, `/clear`, `/exit`. Код виходу 0.

| Крок | Результат |
|---|---|
| заголовок | `model qwen3.5:4b, thinking off, num_ctx 16384` |
| `/context` до питань | `No context has been sent to the model yet.` |
| «Що зараз відбувається із системою?» | українською; «стан системи **невідомий**, дані застаріли (3.1 дні)», останні значення названі як збережені, не поточні; причин не вигадує. `prompt 969, output 412 tokens, 10.5 s` |
| «Яка причина високого CPU?» | «**неможливо визначити причину**»; PSI прочитано правильно («за останні 300 секунд … 0.00 %»); «кореляція не є причиною». `prompt 1397, output 519 tokens, 10.3 s` |
| `/context` | 2 system (промпт + Monitoring context, freshness першим рядком, PSI один раз з новими підписами) + історія + останнє питання |
| `/clear`, `/exit` | працюють |
| міркування у виводі | немає |

Додатково один запит з `think=true`: відповідь українською, `prompt 967, output 2253 tokens, 43.0 s, thinking 5885 chars (not shown)`; у виводі немає ні тексту міркувань, ні `<think>`.

`prompt_eval_count` для першого питання (969) значно менший за `num_ctx` (16 384); старий JSON-контекст на тих самих даних давав 9 836.

### Що помічено й не виправлялось (для Завдання 7)

- Граматика: «зафіксовано жодних аномалій» (пропущено «не») повторюється в обох режимах; русизм «очередей».
- На «Яка причина високого CPU?» модель не запропонувала конкретну read-only перевірку процесів (лише «оновити дані моніторингу»); з `think=true` запропонувала `top`/`htop`.
- `analyze --context` на порожній базі показує `analyzed unknown to unknown` у блоці historical — коректно, але незграбно.
- Не перевірено на реальній моделі без підтримки thinking (такої немає локально): `THINKING_NOT_SUPPORTED` покрито тільки мок-тестом; текст помилки Ollama — припущення.
