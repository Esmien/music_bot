-- ===================================
-- Benchmark: IX_GENERATIONS_USER_STATUS_CREATED
-- ===================================
-- Проверяет, что композитный индекс работает на запросе из save_feedback()

BEGIN;

-- 1. Временная таблица с тестовыми данными (используем VARCHAR вместо ENUM)
CREATE TEMP TABLE benchmark_generations (
    id INTEGER PRIMARY KEY,
    prompt TEXT NOT NULL,
    enriched_prompt JSONB NOT NULL,
    title VARCHAR,
    created_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(16) NOT NULL,  -- вместо ENUM используем VARCHAR
    user_id INTEGER NOT NULL
);

-- Генерим 100k записей
INSERT INTO benchmark_generations
SELECT
    id,
    'prompt_' || id,
    '{"theme": "rock", "mood": "energetic"}'::jsonb,
    'Song Title ' || id,
    now() - (random() * interval '365 days'),
    CASE 
        WHEN random() < 0.7 THEN 'success'
        WHEN random() < 0.9 THEN 'pending'
        ELSE 'failed'
    END,
    1000000 + (random() * 50000)::int
FROM generate_series(1, 100000) AS id;

ANALYZE benchmark_generations;

-- 2. Запрос БЕЗ индекса (для сравнения)
\echo '========================================'
\echo 'WITHOUT INDEX (Seq Scan):'
\echo '========================================'
EXPLAIN ANALYZE
SELECT *
FROM benchmark_generations
WHERE user_id = 1025000
  AND status = 'success'
ORDER BY created_at DESC, id DESC
LIMIT 1;

-- 3. Создаём индекс и повторяем запрос
\echo ''
\echo '========================================'
\echo 'WITH INDEX (Index Scan):'
\echo '========================================'
CREATE INDEX idx_bench_composite ON benchmark_generations(user_id, status, created_at);
ANALYZE benchmark_generations;

EXPLAIN ANALYZE
SELECT *
FROM benchmark_generations
WHERE user_id = 1025000
  AND status = 'success'
ORDER BY created_at DESC, id DESC
LIMIT 1;

-- 4. Проверка на реальной таблице (если уже есть данные)
\echo ''
\echo '========================================'
\echo 'PRODUCTION TABLE (generations):'
\echo '========================================'

DO $
DECLARE
    test_user_id INTEGER;
BEGIN
    -- Берём реального юзера из таблицы
    SELECT user_id INTO test_user_id FROM generations LIMIT 1;
    
    IF test_user_id IS NOT NULL THEN
        RAISE NOTICE 'Testing with user_id: %', test_user_id;
    ELSE
        RAISE NOTICE 'No data in generations table yet';
    END IF;
END $;

EXPLAIN ANALYZE
SELECT *
FROM generations
WHERE user_id = (SELECT user_id FROM generations LIMIT 1)
  AND status = 'success'
ORDER BY created_at DESC, id DESC
LIMIT 1;

ROLLBACK;

-- Выводим legend после отката
\echo ''
\echo '========================================'
\echo 'ЧТО ИСКАТЬ В ВЫВОДЕ:'
\echo '========================================'
\echo '✅ Index Scan using ix_generations_user_status_created'
\echo '   → индекс используется, всё ок'
\echo ''
\echo '❌ Seq Scan on generations'
\echo '   → PostgreSQL игнорирует индекс'
\echo ''
\echo 'Execution Time:'
\echo '  < 1ms     → отлично'
\echo '  1-10ms    → норма для 100k+ строк'
\echo '  > 50ms    → проблема (нет индекса или неоптимальный)'