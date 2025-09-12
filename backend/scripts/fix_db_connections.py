#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СКРИПТ ДЛЯ ВИПРАВЛЕННЯ ПРОБЛЕМ З З'ЄДНАННЯМИ БД
Перевіряє та налаштовує PostgreSQL для роботи з паралельною обробкою
"""

import os
import sys
import psycopg2
import logging
from dotenv import load_dotenv

# Додаємо шлях до backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Параметри підключення
DB_NAME = os.getenv("DB_NAME", "bsstorage")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

def check_postgres_config():
    """Перевіряє конфігурацію PostgreSQL."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cursor = conn.cursor()
        
        logger.info("✅ Підключення до PostgreSQL успішне")
        
        # Перевіряємо поточні налаштування
        queries = {
            "max_connections": "SHOW max_connections;",
            "shared_buffers": "SHOW shared_buffers;",
            "work_mem": "SHOW work_mem;",
            "maintenance_work_mem": "SHOW maintenance_work_mem;",
            "effective_cache_size": "SHOW effective_cache_size;",
            "random_page_cost": "SHOW random_page_cost;",
            "checkpoint_completion_target": "SHOW checkpoint_completion_target;",
            "wal_buffers": "SHOW wal_buffers;",
            "default_statistics_target": "SHOW default_statistics_target;"
        }
        
        logger.info("\n📊 ПОТОЧНІ НАЛАШТУВАННЯ POSTGRESQL:")
        logger.info("=" * 50)
        
        for param, query in queries.items():
            cursor.execute(query)
            result = cursor.fetchone()[0]
            logger.info(f"{param:25}: {result}")
        
        # Перевіряємо активні з'єднання
        cursor.execute("""
            SELECT count(*) as active_connections,
                   max_conn.setting as max_connections,
                   count(*) * 100.0 / max_conn.setting::int as percentage_used
            FROM pg_stat_activity psa,
                 (SELECT setting FROM pg_settings WHERE name = 'max_connections') max_conn
            WHERE psa.state = 'active';
        """)
        
        active, max_conn, percentage = cursor.fetchone()
        logger.info(f"\n🔗 АКТИВНІ З'ЄДНАННЯ:")
        logger.info(f"Активних: {active}/{max_conn} ({percentage:.1f}%)")
        
        # Перевіряємо з'єднання по базах
        cursor.execute("""
            SELECT datname, count(*) as connections
            FROM pg_stat_activity 
            WHERE datname IS NOT NULL
            GROUP BY datname
            ORDER BY connections DESC;
        """)
        
        logger.info(f"\n📈 З'ЄДНАННЯ ПО БАЗАХ:")
        for db_name, conn_count in cursor.fetchall():
            logger.info(f"{db_name:20}: {conn_count} з'єднань")
        
        cursor.close()
        conn.close()
        
        return int(max_conn), int(active)
        
    except Exception as e:
        logger.error(f"❌ Помилка підключення до PostgreSQL: {e}")
        return None, None

def get_recommended_settings():
    """Повертає рекомендовані налаштування для PostgreSQL."""
    logger.info("\n💡 РЕКОМЕНДОВАНІ НАЛАШТУВАННЯ:")
    logger.info("=" * 50)
    
    recommendations = {
        "max_connections": "200",  # Збільшено з стандартних 100
        "shared_buffers": "256MB",  # 25% від RAM (якщо 1GB RAM)
        "work_mem": "4MB",  # Для кожного з'єднання
        "maintenance_work_mem": "64MB",  # Для обслуговування
        "effective_cache_size": "512MB",  # 50% від RAM
        "checkpoint_completion_target": "0.9",  # Оптимізація записів
        "wal_buffers": "16MB",  # Для WAL
        "random_page_cost": "1.1",  # Для SSD
        "default_statistics_target": "100"  # Статистика
    }
    
    for param, value in recommendations.items():
        logger.info(f"{param:25}: {value}")
    
    logger.info("\n📝 ДЛЯ ЗАСТОСУВАННЯ ДОДАЙТЕ В postgresql.conf:")
    for param, value in recommendations.items():
        logger.info(f"{param} = {value}")

def kill_idle_connections():
    """Завершує неактивні з'єднання."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cursor = conn.cursor()
        
        # Знаходимо неактивні з'єднання старші 10 хвилин
        cursor.execute("""
            SELECT pid, state, state_change, query
            FROM pg_stat_activity 
            WHERE state = 'idle'
              AND state_change < NOW() - INTERVAL '10 minutes'
              AND datname = %s
              AND pid != pg_backend_pid();
        """, (DB_NAME,))
        
        idle_connections = cursor.fetchall()
        
        if idle_connections:
            logger.info(f"\n🧹 ОЧИЩЕННЯ НЕАКТИВНИХ З'ЄДНАНЬ:")
            logger.info(f"Знайдено {len(idle_connections)} неактивних з'єднань")
            
            for pid, state, state_change, query in idle_connections:
                logger.info(f"Завершення PID {pid} (неактивний з {state_change})")
                cursor.execute("SELECT pg_terminate_backend(%s)", (pid,))
            
            conn.commit()
            logger.info("✅ Неактивні з'єднання завершені")
        else:
            logger.info("✅ Неактивних з'єднань не знайдено")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Помилка очищення з'єднань: {e}")

def main():
    """Головна функція."""
    logger.info("🔧 ДІАГНОСТИКА ТА ВИПРАВЛЕННЯ З'ЄДНАНЬ БД")
    logger.info("=" * 60)
    
    # Крок 1: Перевіряємо конфігурацію
    max_conn, active_conn = check_postgres_config()
    
    if max_conn is None:
        logger.error("❌ Не вдалося підключитися до БД")
        return
    
    # Крок 2: Аналізуємо проблему
    if active_conn > max_conn * 0.8:
        logger.warning(f"⚠️ УВАГА: Використовується {active_conn}/{max_conn} з'єднань ({active_conn/max_conn*100:.1f}%)")
        logger.warning("Це може призвести до помилки 'too many clients'")
    
    # Крок 3: Очищуємо неактивні з'єднання
    kill_idle_connections()
    
    # Крок 4: Показуємо рекомендації
    get_recommended_settings()
    
    logger.info("\n🎯 ШВИДКЕ РІШЕННЯ:")
    logger.info("1. Перезапустіть програму (закрийте та відкрийте знову)")
    logger.info("2. Якщо проблема повториться - збільшіть max_connections в PostgreSQL")
    logger.info("3. Або зменшіть кількість паралельних воркерів в config.py")

if __name__ == "__main__":
    main()
