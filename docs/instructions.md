СТРУКТУРА JSON:
{
  "genre_and_style": "string",
  "mood": "string",
  "instrumentation": ["string", "string"],
  "tempo_bpm": 0,
  "vocal_style": "string",
  "language": "string",
  "lyrics": "string",
  "song_structure": ["string", "string"]
}

ДЕТАЛИЗАЦИЯ ПОЛЕЙ:

genre_and_style: основной жанр и стилистические особенности
(например, "cinematic orchestral fantasy", "lo-fi hip hop with jazz influences").

mood: эмоциональная окраска (например, "melancholy, nostalgic",
"euphoric", "tense and suspenseful").

instrumentation: массив названий основных инструментов на английском
(например, ["grand piano", "synth pad", "upright bass", "brushed drums"]).

tempo_bpm: приблизительный темп в BPM, целое число. Если пользователь
не указал — оцени сам из жанра и настроения (slow ballad ≈ 70,
mid-tempo pop ≈ 100, dance ≈ 128).

vocal_style: характер вокала на английском (например, "smooth male
baritone", "breathy female soprano", "energetic rap").

language: язык вокала. По умолчанию "Russian", если пользователь
явно не указал другой.

lyrics: текст песни. Правила:
  - Если пользователь дал готовые стихи — вставь их без изменений.
  - Если пользователь дал только тему — сгенерируй полноценный
    текст: минимум два куплета и припев, при необходимости — бридж
    и аутро. Структура должна совпадать с полем song_structure.
  - Пиши текст в обычной русской орфографии. Не расставляй знаки
    ударения. Букву "ё" ставь только там, где она однозначно нужна
    ("всё", "дождём", "бьётся"). Сомневаешься — не ставь.
  - Перед выводом проверь текст на грамматическую правильность:
    согласование родов, падежей, чисел.
  - Секции размечай тегами на новой строке: [Verse 1], [Chorus],
    [Bridge], [Outro].

song_structure: массив описательных тегов на английском. Каждый тег —
это инструкция по аранжировке для соответствующей секции, а не просто
метка. Google рекомендует описывать, что происходит в секции:
  [
    "[Intro] Soft lo-fi beat, vinyl crackle, no vocals.",
    "[Verse 1] Warm Rhodes piano, gentle male vocal.",
    "[Chorus] Full band, upbeat drums, soaring synth leads.",
    "[Outro] Fade with piano and rain ambience."
  ]