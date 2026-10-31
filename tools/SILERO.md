# Silero TTS — локальный голос Юны (macOS arm64)

`tools/silero_server.py` — маленький HTTP-сайдкар на `127.0.0.1:8811`. Ядро (Rust) шлёт ему текст и получает WAV.
Работает полностью офлайн после первой загрузки модели, бесплатно, на CPU.

## API

| Метод | Путь | Тело / ответ |
|---|---|---|
| GET | `/health` | `{"ok":true,"engine":"silero","mock":false,"modelLoaded":false}` |
| POST | `/tts` | `{"text":"...","speaker":"xenia","sample_rate":48000}` → `audio/wav` (PCM 16 бит, моно) |

Ограничения: `text` ≤ 1000 символов; `speaker` ∈ `aidar, baya, kseniya, xenia, eugene`;
`sample_rate` ∈ `8000, 24000, 48000`. Ошибки — `400 {"error":"..."}`.
Текст чистится: остаются кириллица/латиница/цифры/пунктуация, эмодзи и прочее выкидываются,
числа превращаются в слова («21» → «двадцать один»). Латиницу русская модель читает плохо — это нормально.

## Установка (один раз)

Нужны: Homebrew-`uv` (уже стоит) и ~1 ГБ на диске.

```bash
cd yuna
npm run silero   # первый раз: создаёт .venv-silero (Python 3.12), ставит torch/numpy (~400 МБ), затем запускает сайдкар
```

(Torch пока не поддерживает системный Python 3.15, поэтому отдельное окружение на 3.12.)

На Apple Silicon колесо `torch` с PyPI уже без CUDA — отдельный CPU-индекс не нужен.
(На Linux для CPU-only: `uv pip install torch --index-url https://download.pytorch.org/whl/cpu`.)

## Запуск

```bash
.venv-silero/bin/python tools/silero_server.py
# или с параметрами:
SILERO_SPEAKER=baya SILERO_PORT=8811 .venv-silero/bin/python tools/silero_server.py
```

Переменные: `SILERO_HOST` (по умолчанию `127.0.0.1`), `SILERO_PORT` (`8811`),
`SILERO_SPEAKER` (`xenia`), `SILERO_MODEL` (`v4_ru`). Те же значения можно передать флагами
`--host --port --speaker --model`.

**Первый запрос** (не старт сервера — модель грузится лениво) скачает один файл модели
`https://models.silero.ai/models/tts/ru/v4_ru.pt` (~40 МБ) в `.cache/silero/v4_ru.pt` внутри проекта.
Свой путь к файлу — `SILERO_MODEL_PATH=/путь/к/v4_ru.pt`.

> ⚠️ Ночью 29.10 с этой машины `models.silero.ai` не отвечал (таймаут), при этом GitHub был доступен.
> Если повторится: скачайте файл вручную (браузер/VPN) и положите в `.cache/silero/v4_ru.pt`.

Первый ответ займёт 10–60 с, дальше — обычно 0.2–1 с на фразу на M-серии.

## Проверка

```bash
curl -s http://127.0.0.1:8811/health
curl -s -X POST http://127.0.0.1:8811/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"Привет, чат! Юна на связи.","speaker":"xenia","sample_rate":48000}' \
  -o /tmp/mira.wav && afplay /tmp/mira.wav
```

Без torch можно проверить всю цепочку в режиме-заглушке (вместо речи — бипы):

```bash
python3 tools/silero_server.py --mock
```

Подключение к ядру: в `.env` поставить `TTS_PROVIDER=silero` (и, если менялся порт, `SILERO_URL=http://127.0.0.1:8811`)
— точные имена переменных см. в README ядра.

## Безопасность / доверие

- `torch.hub` **не используется**: код с GitHub не скачивается и не исполняется, модель грузится
  напрямую через `torch.package` (так рекомендует сам README Silero).
- Но файл модели `.pt` — это torch.package (внутри pickle и код), то есть тоже исполняемый.
  Загружайте его только с официального `models.silero.ai`.
- Сайдкар без авторизации — держите его на `127.0.0.1` (по умолчанию так и есть, при другом хосте в логе будет предупреждение).
- Лицензия моделей Silero — CC BY-NC-SA 4.0 (некоммерческое использование). Для монетизируемого стрима
  стоит прочитать условия в репозитории Silero.

## Частые проблемы

| Симптом | Решение |
|---|---|
| `503 torch/numpy не установлены` | не тот python: запускайте через `npm run silero` / `.venv-silero/bin/python`, а не системный `python3` |
| `No module named 'omegaconf'` | `npm run silero (ставит зависимости в .venv-silero)` |
| Долго на первом запросе | идёт скачивание модели, смотрите лог сайдкара |
| Ошибка сети при первом запросе | нужен доступ к models.silero.ai один раз, либо положите файл вручную (см. выше) |
| `Address already in use` | порт занят: `SILERO_PORT=8812 ...` и тот же порт в `.env` ядра |
