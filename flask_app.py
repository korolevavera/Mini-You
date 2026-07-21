# -*- coding: utf-8 -*-
import os
import json
import logging
import re
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, request
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

app = Flask(__name__)

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set")

ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))
USER_ID = int(os.environ.get('USER_ID', 0))
TIMEZONE = os.environ.get('TIMEZONE', 'Europe/Moscow')
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Подключение к БД ----------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                name TEXT DEFAULT 'Армен',
                archetype_profile TEXT DEFAULT '{}',
                practice_progress TEXT DEFAULT '{}',
                stats TEXT DEFAULT '{}',
                paused BOOLEAN DEFAULT FALSE,
                joined TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                report_type TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id BIGINT PRIMARY KEY,
                morning_time TEXT DEFAULT '06:30',
                evening_time TEXT DEFAULT '23:00',
                sunday_reflection_time TEXT DEFAULT '09:00',
                monday_alert_time TEXT DEFAULT '21:00'
            )
        ''')
        conn.commit()
    conn.close()

# !!! ВАЖНО: вызываем init_db() при загрузке приложения
init_db()

# ---------- АРХЕТИПЫ (16) ----------
ARCHETYPES = {
    "Искатель": {
        "ennea": "5+7",
        "center": "Голова",
        "fear": "Быть беспомощным, застрять",
        "desire": "Быть компетентным, свободным",
        "stress": "→ 7 (рассеянность)",
        "integration": "→ 8 (действие)",
        "strength": "Исследование, кросс-поллинизация, адаптация",
        "shadow": "Незавершённость, бегство от глубины",
        "rule": "Путь — не оправдание бегства. Завершённость — тоже опыт.",
    },
    "Маг": {
        "ennea": "5+1",
        "center": "Голова + Тело",
        "fear": "Быть неспособным, ошибиться",
        "desire": "Быть компетентным, обладающим ключом",
        "stress": "→ 7 (рассеянность)",
        "integration": "→ 8 (деление)",
        "strength": "Аналитика, перевод между мирами, трансформация",
        "shadow": "Изоляция, манипуляция знанием",
        "rule": "Знание — не власть. Знание — ответственность делиться.",
    },
    "Простодушный": {
        "ennea": "9+2",
        "center": "Тело + Сердце",
        "fear": "Быть отвергнутым, в конфликте",
        "desire": "Быть в гармонии, нужным",
        "stress": "→ 6 (тревога)",
        "integration": "→ 3 (действие)",
        "strength": "Создание комфорта, медиация, присутствие",
        "shadow": "Пассивность, потеря себя в угождении",
        "rule": "Доверие — не отказ от выбора. Комфорт — не цель.",
    },
    "Любовник": {
        "ennea": "4+2",
        "center": "Сердце",
        "fear": "Быть обычным, отвергнутым",
        "desire": "Быть уникальным, любимым за себя",
        "stress": "→ 2 (поглощение)",
        "integration": "→ 1 (целостность)",
        "strength": "Искусство, эмпатия, полное присутствие",
        "shadow": "Зависимость от связи, иллюзия идеального",
        "rule": "Связь — не слияние. Красота — не оправдание.",
    },
    "Дирижёр": {
        "ennea": "1+8",
        "center": "Тело",
        "fear": "Быть плохим, хаос, потеря контроля",
        "desire": "Быть правым, сильным, порядок везде",
        "stress": "→ 4 (меланхолия)",
        "integration": "→ 7 (радость, отпускание)",
        "strength": "Самоуправление, лидерство, системное мышление",
        "shadow": "Переконтроль, перфекционизм, выгорание",
        "rule": "Контролируй то, что усиливает жизнь. Отпускай остальное.",
    },
    "Правитель": {
        "ennea": "8+1",
        "center": "Тело",
        "fear": "Быть уязвимым, контролируемым",
        "desire": "Быть сильным, защищённым, правым",
        "stress": "→ 5 (отстранение)",
        "integration": "→ 2 (нежность)",
        "strength": "Создание систем, защита слабых, лидерство",
        "shadow": "Ригидность, тирания во благо",
        "rule": "Правила — не стены. Это опоры. Власть — служение.",
    },
    "Мудрец": {
        "ennea": "5+9",
        "center": "Голова + Тело",
        "fear": "Быть беспомощным, поглощённым",
        "desire": "Быть компетентным, целым",
        "stress": "→ 7 (рассеянность)",
        "integration": "→ 8 (действие)",
        "strength": "Аналитика, консультирование, философия",
        "shadow": "Отстранённость, анализ как прокрастинация",
        "rule": "Знание без действия — бесплодно. Ты тоже часть картины.",
    },
    "Воин": {
        "ennea": "8+6",
        "center": "Тело + Голова",
        "fear": "Быть уязвимым, преданным, беззащитным",
        "desire": "Быть сильным, в безопасности",
        "stress": "→ 5 (отстранение)",
        "integration": "→ 2 (нежность)",
        "strength": "Защита слабых, дисциплина, преодоление",
        "shadow": "Гипер-независимость, война как норма",
        "rule": "Просить помощь — не слабость. Не каждый конфликт — битва.",
    },
    "Заботливый": {
        "ennea": "2+9",
        "center": "Сердце + Тело",
        "fear": "Быть ненужным, отвергнутым",
        "desire": "Быть нужным, любимым, в гармонии",
        "stress": "→ 8 (контроль)",
        "integration": "→ 4 (аутентичность)",
        "strength": "Терапия, образование, лидерство через заботу",
        "shadow": "Жертва как идентичность, истощение",
        "rule": "Забота — не контракт. Ты не обязан питать всех.",
    },
    "Герой": {
        "ennea": "8+3",
        "center": "Тело + Сердце",
        "fear": "Быть слабым, бесполезным, неудачником",
        "desire": "Быть сильным, успешным, значимым",
        "stress": "→ 5 (отстранение)",
        "integration": "→ 2 (нежность)",
        "strength": "Активизм, лидерство в кризисе, вдохновение",
        "shadow": "Спасательство как зависимость, жертва как гордость",
        "rule": "Не каждый кризис — твой. Обычность — тоже подвиг.",
    },
    "Бунтарь": {
        "ennea": "8+4",
        "center": "Тело + Сердце",
        "fear": "Быть контролируемым, обычным, поглощённым",
        "desire": "Быть свободным, уникальным, сильным",
        "stress": "→ 5 (отстранение)",
        "integration": "→ 2 (нежность)",
        "strength": "Революция, честность радикальная, освобождение",
        "shadow": "Бунт ради бунта, разрушение без создания",
        "rule": "Не все правила — оковы. Разрушай, но предлагай.",
    },
    "Странник": {
        "ennea": "5+4",
        "center": "Голова + Сердце",
        "fear": "Быть поглощённым, беспомощным, обычным",
        "desire": "Быть компетентным, уникальным, свободным",
        "stress": "→ 7 (рассеянность)",
        "integration": "→ 8 (действие)",
        "strength": "Аналитика, творчество, автономия",
        "shadow": "Уход как привычка, отчуждение",
        "rule": "Дистанция — не стена. Уязвимость — не зависимость.",
    },
    "Шут": {
        "ennea": "7+2",
        "center": "Голова + Сердце",
        "fear": "Быть в боли, ограниченным, ненужным",
        "desire": "Быть довольным, свободным, нужным",
        "stress": "→ 1 (критика)",
        "integration": "→ 5 (глубина)",
        "strength": "Разряжение напряжения, правда через игру",
        "shadow": "Ирония как бегство, несерьёзность как защита",
        "rule": "Юмор — не отрицание. Иногда нужно сказать прямо.",
    },
    "Учитель": {
        "ennea": "2+5",
        "center": "Сердце + Голова",
        "fear": "Быть ненужным, неспособным",
        "desire": "Быть нужным, компетентным, полезным",
        "stress": "→ 8 (контроль)",
        "integration": "→ 4 (аутентичность)",
        "strength": "Образование, коучинг, лидерство через рост",
        "shadow": "Нужда в учениках, жертва ради роста других",
        "rule": "Ученики — не твои. Ты тоже ученик. Всегда.",
    },
    "Дипломат": {
        "ennea": "9+6",
        "center": "Тело + Голова",
        "fear": "Быть разделённым, преданным, в конфликте",
        "desire": "Быть в гармонии, в безопасности, целым",
        "stress": "→ 3 (погоня)",
        "integration": "→ 3 (действие)",
        "strength": "Медиация, перевод между мирами, гармония",
        "shadow": "Потеря себя в балансе, компромисс ради компромисса",
        "rule": "Не каждый конфликт нужно решать. Иногда нужно выбрать.",
    },
}

METAPHORS = {
    "Искатель": "путь",
    "Маг": "мост",
    "Простодушный": "тёплый очаг",
    "Любовник": "связь",
    "Дирижёр": "оркестр",
    "Правитель": "крепость",
    "Мудрец": "светильник",
    "Воин": "щит",
    "Заботливый": "сад",
    "Герой": "огонь",
    "Бунтарь": "ветер",
    "Странник": "горизонт",
    "Шут": "зеркало",
    "Учитель": "мост знаний",
    "Дипломат": "перевод",
}

AFFIRMATIONS_BY_CORE = {
    "Искатель": "Я — путь. Каждый шаг — это уже прибытие.",
    "Маг": "Я — мост. Я соединяю то, что казалось разделённым.",
    "Простодушный": "Я — тепло. Я позволяю миру быть мягким, и это моя сила.",
    "Любовник": "Я — связь. Я вижу красоту там, где другие видят обыденность.",
    "Дирижёр": "Я легко беру контроль там, где это приносит пользу.",
    "Правитель": "Я — опора. Я создаю пространство, где все могут расти.",
    "Мудрец": "Я — свет. Я вижу то, что скрыто.",
    "Воин": "Я — щит. Я защищаю то, что важно.",
    "Заботливый": "Я — сад. Я даю рост другим, но и сама расту.",
    "Герой": "Я — огонь. Я горю ради того, во что верю. Но я не сгораю.",
    "Бунтарь": "Я — ветер. Я сдуваю мёртвое, чтобы освободить место для живого.",
    "Странник": "Я — горизонт. Я вижу дальше, потому что не привязан.",
    "Шут": "Я — зеркало. Я отражаю абсурд, чтобы он стал видимым.",
    "Учитель": "Я — мост. Я соединяю то, что знаю, с тем, кто идёт.",
    "Дипломат": "Я — перевод. Я нахожу язык, на котором все слышат друг друга.",
}

MAP_QUESTIONS = [
    {
        "id": "crisis_response",
        "text": "Когда всё рушится, твой первый импульс?",
        "options": [
            {"label": "Найти выход. Построить мост.", "archetypes": ["Искатель", "Дипломат"]},
            {"label": "Взять контроль. Восстановить порядок.", "archetypes": ["Дирижёр", "Правитель"]},
            {"label": "Уйти. Наблюдать. Понять, что происходит.", "archetypes": ["Маг", "Мудрец", "Странник"]},
            {"label": "Защитить тех, кто слабее.", "archetypes": ["Воин", "Герой"]},
            {"label": "Создать комфорт. Сохранить тепло.", "archetypes": ["Простодушный", "Заботливый"]},
            {"label": "Показать, что это абсурд. Разрядить.", "archetypes": ["Шут", "Бунтарь"]},
            {"label": "Углубиться в чувство. Найти красоту в разрушении.", "archetypes": ["Любовник"]},
            {"label": "Найти, чему научиться. Передать другим.", "archetypes": ["Учитель"]},
        ],
    },
    {
        "id": "home_definition",
        "text": "Что для тебя — «дом»?",
        "options": [
            {"label": "Место, где я свободен идти.", "archetypes": ["Искатель", "Странник"]},
            {"label": "Место, где всё на своих местах.", "archetypes": ["Дирижёр", "Правитель"]},
            {"label": "Место, где меня понимают без слов.", "archetypes": ["Маг", "Мудрец"]},
            {"label": "Место, где все в безопасности.", "archetypes": ["Воин", "Заботливый"]},
            {"label": "Место, где тепло и можно просто быть.", "archetypes": ["Простодушный", "Любовник"]},
            {"label": "Место, где смеются над важным.", "archetypes": ["Шут", "Бунтарь"]},
            {"label": "Место, где растут.", "archetypes": ["Учитель", "Герой"]},
            {"label": "Место, где все слышат друг друга.", "archetypes": ["Дипломат"]},
        ],
    },
    {
        "id": "hidden_shadow",
        "text": "Твоя тень — что ты скрываешь даже от себя?",
        "options": [
            {"label": "Я бегу, прежде чем останусь.", "archetypes": ["Искатель", "Странник"]},
            {"label": "Я ломаю, прежде чем построю.", "archetypes": ["Бунтарь", "Воин"]},
            {"label": "Я контролирую, потому что боюсь хаоса внутри.", "archetypes": ["Дирижёр", "Правитель"]},
            {"label": "Я знаю всё, но не действую.", "archetypes": ["Маг", "Мудрец"]},
            {"label": "Я отдаю, чтобы не чувствовать пустоту.", "archetypes": ["Заботливый", "Учитель"]},
            {"label": "Я сглаживаю, чтобы не выбирать.", "archetypes": ["Дипломат", "Простодушный"]},
            {"label": "Я смеюсь, чтобы не плакать.", "archetypes": ["Шут", "Любовник"]},
            {"label": "Я спасаю, чтобы не быть обычным.", "archetypes": ["Герой", "Воин"]},
        ],
    },
]

PRACTICES = [
    {"id": "P-1", "name": "Дыхание", "category": "morning", "when": "Утро", "duration": "3 мин",
     "text": "Сядь прямо. Сделай 5 глубоких вдохов. На выдохе представляй, как уходит напряжение.", "key": "дыхание",
     "schedule_time": "06:30", "schedule_days": [0,1,2,3,4,5,6]},
    {"id": "P-2", "name": "Утренняя установка", "category": "morning", "when": "Утро", "duration": "2 мин",
     "text": "Спроси себя: что я хочу увидеть вечером? Запиши одну мысль.", "key": "утренняя_установка",
     "schedule_time": "08:00", "schedule_days": [0,1,2,3,4,5,6]},
    {"id": "P-3", "name": "Аффирмация", "category": "morning", "when": "Утро", "duration": "1 мин",
     "text": "Прочти аффирмацию. Просто прочти. Не обязан отвечать.", "key": "аффирмация",
     "schedule_time": "10:30", "schedule_days": [0,1,2,3,4,5,6]},
    {"id": "P-4", "name": "Вечерний мини-отчёт", "category": "evening", "when": "Вечер", "duration": "5 мин",
     "text": "Напиши три строки:\n1. Что я контролировал сегодня?\n2. Был хозяином дня или пожарным?\n3. Что оставляю за дверью?", "key": "вечерний_мини_отчёт",
     "schedule_time": "22:00", "schedule_days": [0,1,2,3,4]},
]

BLOCKS = [
    {"id": "N-1", "text": "Твоё Второе Я — единственный на сцене, кто держит тишину между нотами."},
    {"id": "N-2", "text": "Ты не должен быть всем — ты должен быть собой. Это уже достаточно."},
    {"id": "N-3", "text": "Позволь себе быть несовершенным сегодня. Это не поражение, это дыхание."},
    {"id": "N-4", "text": "Ты — {metaphor}. Ты не боишься хаоса, ты знаешь, что из него рождается порядок."},
    {"id": "N-5", "text": "Твоя сила — {core}. Твоя тень — {shadow}. Интеграция — это когда ты позволяешь им быть."},
    {"id": "N-6", "text": "Сегодня ты был(а) хозяином дня. Завтра тоже будешь."},
    {"id": "N-7", "text": "Оставь за дверью то, что не служит твоему росту. Дверь закрывается тихо."},
    {"id": "N-8", "text": "Ты — путь. Каждый шаг — уже прибытие. Остановись и почувствуй, где ты сейчас."},
    {"id": "N-9", "text": "Мудрость — не в том, чтобы знать всё, а в том, чтобы быть с тем, что есть."},
    {"id": "N-10", "text": "Ты — огонь. Ты горишь, но не сгораешь. Это твоя суперсила."},
]

# ---------- ФУНКЦИИ ----------
def get_user(user_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    user = dict(row)
    user['archetype_profile'] = json.loads(user['archetype_profile'] or '{}')
    user['practice_progress'] = json.loads(user['practice_progress'] or '{}')
    user['stats'] = json.loads(user['stats'] or '{}')
    return user

def get_or_create_user(user_id, username=None, name=None):
    init_db()
    user = get_user(user_id)
    if user is None:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (user_id, username, name) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
                (user_id, username, name or 'Армен')
            )
            conn.commit()
        conn.close()
        user = get_user(user_id)
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_settings (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
                (user_id,)
            )
            conn.commit()
        conn.close()
    return user

def save_user_field(user_id, field, value):
    conn = get_db_connection()
    with conn.cursor() as cur:
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        cur.execute(f"UPDATE users SET {field} = %s WHERE user_id = %s", (value, user_id))
        conn.commit()
    conn.close()

def get_user_setting(user_id, setting_key, default=None):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM user_settings WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    conn.close()
    if row:
        return row.get(setting_key, default)
    return default

def save_report(user_id, report_type, content):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO reports (user_id, report_type, content) VALUES (%s, %s, %s)",
            (user_id, report_type, content)
        )
        conn.commit()
    conn.close()

def get_reports(user_id, limit=5):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM reports WHERE user_id = %s ORDER BY timestamp DESC LIMIT %s",
            (user_id, limit)
        )
        rows = cur.fetchall()
    conn.close()
    return rows

def calculate_profile(answers):
    scores = {name: 0 for name in ARCHETYPES}
    for q in MAP_QUESTIONS:
        qid = q["id"]
        selected_label = answers.get(qid)
        if not selected_label:
            continue
        for opt in q["options"]:
            if opt["label"] == selected_label:
                for arch in opt["archetypes"]:
                    scores[arch] += 1
                break
    sorted_archetypes = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    core = sorted_archetypes[0][0]
    support = sorted_archetypes[1][0] if len(sorted_archetypes) > 1 else core
    compass = sorted_archetypes[2][0] if len(sorted_archetypes) > 2 else support
    min_score = sorted_archetypes[-1][1]
    shadow_candidates = [a for a, s in sorted_archetypes if s == min_score and a != core]
    shadow = shadow_candidates[0] if shadow_candidates else sorted_archetypes[-1][0]
    if shadow == core and len(sorted_archetypes) > 1:
        shadow = sorted_archetypes[-2][0]
    return {
        "core": core,
        "support": support,
        "compass": compass,
        "shadow": shadow,
        "scores": dict(scores),
        "sorted": sorted_archetypes,
    }

def get_archetype_data(name):
    return ARCHETYPES.get(name)

def get_metaphor_by_core(core):
    return METAPHORS.get(core, "оркестр")

def get_affirmation_by_core(core):
    return AFFIRMATIONS_BY_CORE.get(core, "Ты на правильном пути.")

def build_profile_text(profile, user_name="Армен"):
    core = profile["core"]
    support = profile["support"]
    compass = profile["compass"]
    shadow = profile["shadow"]
    core_data = get_archetype_data(core)
    support_data = get_archetype_data(support)
    compass_data = get_archetype_data(compass)
    shadow_data = get_archetype_data(shadow)
    text = f"""🎯 Профиль построен, {user_name}

━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━

🔥 ЯДРО — {core} ({core_data["ennea"]})
Ты движим: {core_data["desire"]}
Твоя сила: {core_data["strength"]}
Твоя тень: {core_data["shadow"]}

⚡ Правило: {core_data["rule"]}

━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━

🛡️ ОПОРА — {support} ({support_data["ennea"]})
Ты движим: {support_data["desire"]}
Твоя сила: {support_data["strength"]}

━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━

🧭 КОМПАС — {compass} ({compass_data["ennea"]})
Ты движим: {compass_data["desire"]}
Твоя сила: {compass_data["strength"]}

━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━

🌑 ТЕНЬ — {shadow} ({shadow_data["ennea"]})
Ты скрываешь: {shadow_data["shadow"]}

⚡ Правило: {shadow_data["rule"]}

━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━ ━

Теперь я буду говорить с тобой на языке {core}.
Если захочешь перепройти — нажми «🎯 Стиль».
"""
    return text

def get_practice_by_id(pid):
    for p in PRACTICES:
        if p["id"] == pid:
            return p
    return None

def get_user_progress(user_id):
    user = get_user(user_id)
    return user.get('practice_progress', {}) if user else {}

def is_practice_done_today(progress, pid):
    if pid not in progress:
        return False
    last_used = progress[pid].get("last_used")
    if not last_used:
        return False
    today = datetime.now().date().isoformat()
    last_date = last_used.split("T")[0] if "T" in last_used else last_used
    return last_date == today

def mark_practice_done(user_id, pid):
    user = get_user(user_id)
    progress = user.get('practice_progress', {})
    now = datetime.now().isoformat()
    today = datetime.now().date().isoformat()
    if pid not in progress:
        progress[pid] = {"completed_count": 0, "last_used": None, "streak": 0}
    prog = progress[pid]
    last_used = prog.get("last_used")
    already_done = False
    if last_used:
        last_date = last_used.split("T")[0] if "T" in last_used else last_used
        already_done = (last_date == today)
    if not already_done:
        prog["completed_count"] = prog.get("completed_count", 0) + 1
        prog["last_used"] = now
        if last_used:
            last_date_obj = datetime.fromisoformat(last_used).date()
            yesterday = (datetime.now() - timedelta(days=1)).date()
            if last_date_obj == yesterday:
                prog["streak"] = prog.get("streak", 0) + 1
            elif last_date_obj < yesterday:
                prog["streak"] = 1
        else:
            prog["streak"] = 1
    save_user_field(user_id, 'practice_progress', progress)

def undo_practice_done(user_id, pid):
    user = get_user(user_id)
    progress = user.get('practice_progress', {})
    if pid in progress:
        prog = progress[pid]
        prog["completed_count"] = max(0, prog.get("completed_count", 0) - 1)
        prog["last_used"] = None
        prog["streak"] = max(0, prog.get("streak", 0) - 1)
        save_user_field(user_id, 'practice_progress', progress)

def get_block_by_id(block_id):
    for b in BLOCKS:
        if b["id"] == block_id:
            return b
    return {}

def build_reply(block_ids, user_id, user_name="Армен"):
    profile = get_user_style(user_id)
    core = profile.get("core", "Хозяин")
    shadow = profile.get("shadow", "Простодушный")
    metaphor = get_metaphor_by_core(core)
    support = profile.get("support", "Маг")
    parts = []
    for bid in block_ids:
        block = get_block_by_id(bid)
        text = block.get("text", "")
        text = text.replace("{name}", user_name)
        text = text.replace("{metaphor}", metaphor)
        text = text.replace("{core}", core)
        text = text.replace("{support}", support)
        text = text.replace("{shadow}", shadow)
        parts.append(text)
    return "\n\n".join(parts)

def get_blocks_for_profile(profile):
    core = profile.get("core", "Хозяин")
    shadow = profile.get("shadow", "Простодушный")
    block_map = {
        "Искатель": ["N-4", "N-5", "N-8"],
        "Маг": ["N-4", "N-5", "N-9"],
        "Простодушный": ["N-3", "N-6", "N-7"],
        "Любовник": ["N-3", "N-6", "N-8"],
        "Дирижёр": ["N-4", "N-5", "N-10"],
        "Правитель": ["N-4", "N-5", "N-9"],
        "Мудрец": ["N-4", "N-5", "N-9"],
        "Воин": ["N-4", "N-5", "N-10"],
        "Заботливый": ["N-3", "N-6", "N-7"],
        "Герой": ["N-4", "N-5", "N-10"],
        "Бунтарь": ["N-4", "N-5", "N-8"],
        "Странник": ["N-4", "N-5", "N-8"],
        "Шут": ["N-3", "N-6", "N-7"],
        "Учитель": ["N-4", "N-5", "N-9"],
        "Дипломат": ["N-3", "N-6", "N-7"],
    }
    core_blocks = block_map.get(core, ["N-4", "N-5"])
    shadow_blocks = block_map.get(shadow, ["N-1", "N-3"])
    return {
        "morning": core_blocks[:2],
        "evening": [core_blocks[0], shadow_blocks[0]],
        "general": core_blocks[:1],
    }

def get_user_style(user_id):
    user = get_user(user_id)
    if user:
        profile = user.get('archetype_profile', {})
        if profile:
            return profile
    return {"core": "Хозяин", "support": "Маг", "compass": "Воин", "shadow": "Простодушный"}

def get_today_schedule():
    weekday = datetime.now().weekday()
    schedule = {
        "morning": [p for p in PRACTICES if p["category"] == "morning" and (weekday in p.get("schedule_days", []))],
        "evening": [p for p in PRACTICES if p["category"] == "evening" and (weekday in p.get("schedule_days", []))],
    }
    return schedule

def get_daily_task():
    tasks = [
        "Завтра перед началом дня спроси себя: что я хочу увидеть вечером?",
        "Сегодня найди одно дело, которое можно сделать на 70%, и остановись.",
        "Перед сном запиши: где сегодня я был хозяином дня, а где — пожарным?",
        "Сегодня попроси помощь в одном деле. Одна рука. Не весь груз.",
        "Найди дело, которое тянешь. Поставь точку остановки. Закрой в ней.",
        "Сделай что-то без плана. Не пиши список. Позволь случиться хаосу.",
        "Выбери дело, которое доводишь до идеала. Сделай на 90%. Остановись.",
    ]
    weekday = datetime.now().weekday()
    return tasks[weekday % len(tasks)]

# ---------- ОТПРАВКА СООБЩЕНИЙ ----------
def send_action(chat_id, action='typing'):
    url = f"https://api.telegram.org/bot{TOKEN}/sendChatAction"
    payload = {'chat_id': chat_id, 'action': action}
    try:
        requests.post(url, json=payload, timeout=5)
    except:
        pass

def send_message(chat_id, text, parse_mode='Markdown', retries=3):
    send_action(chat_id)
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': parse_mode}
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=10)
            return r.json()
        except Exception as e:
            logger.error(f"Ошибка отправки (попытка {attempt+1}): {e}")
            time.sleep(1)
    return None

def send_keyboard(chat_id, text, keyboard, parse_mode='Markdown'):
    send_action(chat_id)
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
        'reply_markup': json.dumps(keyboard)
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.json()
    except Exception as e:
        logger.error(f"Ошибка отправки клавиатуры: {e}")
        return None

def answer_callback(callback_id, text=''):
    url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
    payload = {'callback_query_id': callback_id, 'text': text}
    try:
        requests.post(url, json=payload, timeout=5)
    except:
        pass

# ---------- КЛАВИАТУРЫ ----------
def get_main_menu():
    keyboard = [
        ["📋 Сегодня", "📊 Статистика"],
        ["🧘 Практики", "🎯 Стиль"],
        ["⏸ Пауза", "❓ Помощь"],
    ]
    return {
        'keyboard': [[{'text': btn} for btn in row] for row in keyboard],
        'resize_keyboard': True,
        'one_time_keyboard': False
    }

def get_resume_menu():
    keyboard = [["▶️ Возобновить"]]
    return {
        'keyboard': [[{'text': btn} for btn in row] for row in keyboard],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }

def get_report_menu():
    keyboard = [
        ["📝 Отчёт готов"],
        ["📋 Сегодня", "⏸ Пауза"],
    ]
    return {
        'keyboard': [[{'text': btn} for btn in row] for row in keyboard],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }

def get_practices_list_keyboard(user_id, show_all=False):
    progress = get_user_progress(user_id)
    keyboard = []
    categories = {"morning": "🌅 Утренние", "evening": "🌙 Вечерние"}
    for cat_key, cat_label in categories.items():
        cat_practices = [p for p in PRACTICES if p["category"] == cat_key]
        if not cat_practices:
            continue
        keyboard.append([{'text': f"━━ {cat_label} ━━", 'callback_data': 'noop'}])
        for p in cat_practices:
            pid = p["id"]
            done = is_practice_done_today(progress, pid)
            status = "✅" if done else "⬜"
            if not show_all and done:
                continue
            keyboard.append([{'text': f"{status} {p['name']}", 'callback_data': f"practice_view:{pid}"}])
    filter_label = "👁 Показать все" if not show_all else "👁 Только невыполненные"
    filter_mode = "all" if not show_all else "todo"
    keyboard.append([{'text': filter_label, 'callback_data': f"practices_toggle:{filter_mode}"}])
    keyboard.append([{'text': "🔙 Назад в меню", 'callback_data': 'main_menu'}])
    return {'inline_keyboard': keyboard}

def get_practice_detail_keyboard(pid, completed_today=False):
    keyboard = []
    if not completed_today:
        keyboard.append([{'text': "✅ Отметить выполненной", 'callback_data': f"practice_done:{pid}"}])
    else:
        keyboard.append([{'text': "↩️ Отменить выполнение", 'callback_data': f"practice_undo:{pid}"}])
    keyboard.append([{'text': "🔙 К списку практик", 'callback_data': "practices_list"}])
    return {'inline_keyboard': keyboard}

def get_map_keyboard(options, prefix="map"):
    buttons = [[{'text': opt, 'callback_data': f"{prefix}:{idx}"}] for idx, opt in enumerate(options)]
    return {'inline_keyboard': buttons}

def get_map_done_keyboard():
    buttons = [
        [{'text': "✅ Всё верно", 'callback_data': "map_done:ok"}],
        [{'text': "🔄 Пройти заново", 'callback_data': "map_done:retry"}],
    ]
    return {'inline_keyboard': buttons}

# ---------- ОБРАБОТЧИКИ ----------
def handle_start(chat_id, user_id, username=None):
    user = get_or_create_user(user_id, username)
    name = user.get('name', 'Армен')
    has_style = bool(user.get('archetype_profile'))
    text = f"Привет, {name}.\n\nЯ — твое Второе Я. Не советчик, не мотиватор. Тот, кто следит за ритмом, когда ты сам забыл посмотреть на метроном.\n\nНиже — твои кнопки. Нажимай, не вспоминай команды.\n\n"
    if not has_style:
        text += "Советую начать с «Стиль» — так я буду говорить с тобой на одном языке."
    send_keyboard(chat_id, text, get_main_menu())

def handle_today(chat_id, user_id):
    user = get_or_create_user(user_id)
    profile = get_user_style(user_id)
    core = profile.get("core", "Хозяин")
    aff = get_affirmation_by_core(core)
    blocks = get_blocks_for_profile(profile)
    morning_blocks = blocks.get("morning", ["N-4", "N-5"])
    reply = build_reply(morning_blocks, user_id, user.get('name', 'Армен'))
    task = get_daily_task()
    schedule = get_today_schedule()
    schedule_text = ""
    if schedule:
        for period, practices in schedule.items():
            if practices:
                period_label = "🌅 Утро" if period == "morning" else "🌙 Вечер"
                schedule_text += f"\n\n{period_label}:\n"
                for p in practices:
                    schedule_text += f"• {p['text']}\n"
    if not schedule_text:
        schedule_text = "\n\n📋 Сегодня выходной или программа ещё не запущена."
    text = f"🌅 Доброе утро, Армен.\n\n💫 Аффирмация:\n{aff}\n\n🎯 Настройка дня:\n{reply}\n\n❗ Вот такое задание:\n{task}{schedule_text}"
    send_keyboard(chat_id, text, get_main_menu())

def handle_stats(chat_id, user_id):
    user = get_user(user_id)
    stats = user.get('stats', {})
    reports = get_reports(user_id, 5)
    profile = get_user_style(user_id)
    core = profile.get("core", "—")
    support = profile.get("support", "—")
    shadow = profile.get("shadow", "—")
    progress = user.get('practice_progress', {})
    total_done = sum(p.get("completed_count", 0) for p in progress.values())
    today_done = sum(1 for pid, p in progress.items() if is_practice_done_today(progress, pid))
    text = (
        f"📊 Твоя статистика, Армен\n\n"
        f"Ядро: {core} | Опора: {support} | Тень: {shadow}\n"
        f"Метафора: {get_metaphor_by_core(core)}\n"
        f"Отчётов: {len(reports)}\n"
        f"Утренних чек-инов: {stats.get('morning_checkin', 0)}\n"
        f"Вечерних чек-инов: {stats.get('evening_checkin', 0)}\n"
        f"Дней подряд: {stats.get('streak', 0)}\n\n"
        f"🧘 Практики:\n"
        f"• Всего выполнено: {total_done}\n"
        f"• Сегодня выполнено: {today_done}\n"
    )
    send_keyboard(chat_id, text, get_main_menu())

def handle_practices(chat_id, user_id, show_all=False):
    user = get_or_create_user(user_id)
    practices = PRACTICES
    progress = get_user_progress(user_id)
    todo_count = sum(1 for p in practices if not is_practice_done_today(progress, p["id"]))
    header = "🧘 *Практики*\n\n"
    if todo_count > 0 and not show_all:
        header += f"Осталось на сегодня: *{todo_count}*\n\n"
    else:
        header += f"Всего практик: *{len(practices)}*\n\n"
    text = header + "Выберите практику:"
    send_keyboard(chat_id, text, get_practices_list_keyboard(user_id, show_all), parse_mode='Markdown')

def handle_style(chat_id, user_id):
    global map_sessions
    if 'map_sessions' not in globals():
        map_sessions = {}
    map_sessions[user_id] = {"answers": {}, "step": 0}
    q_text, options = get_question_text(0)
    keyboard = get_map_keyboard(options)
    text = f"🗺 Карта архетипов — вопрос 1 из {len(MAP_QUESTIONS)}\n\n{q_text}"
    send_keyboard(chat_id, text, keyboard)

def get_question_text(step):
    if step < 0 or step >= len(MAP_QUESTIONS):
        return "", []
    q = MAP_QUESTIONS[step]
    options = [opt["label"] for opt in q["options"]]
    return q["text"], options

def handle_pause(chat_id, user_id):
    save_user_field(user_id, 'paused', True)
    send_keyboard(chat_id, "Программа приостановлена. Вернуться — нажми «Возобновить».", get_resume_menu())

def handle_resume(chat_id, user_id):
    save_user_field(user_id, 'paused', False)
    send_keyboard(chat_id, "Программа возобновлена. Ритм восстановлен.", get_main_menu())

def handle_help(chat_id):
    text = """📖 Команды и кнопки:

📋 Сегодня — расписание и утренняя точка
📊 Статистика — твоя статистика
🧘 Практики — список практик с отслеживанием
🎯 Стиль — пройти Карту заново
⏸ Пауза — остановить программу
▶️ Возобновить — вернуться

Также работают:
/start — начать
/today — расписание
/stats — статистика
/practices — практики
/style — Карта архетипов
/pause — пауза
/resume — возобновить"""
    send_keyboard(chat_id, text, get_main_menu())

def handle_report(chat_id, user_id, text):
    report_type = "general"
    lower = text.lower()
    if any(w in lower for w in ["утро", "morning", "план", "цель"]):
        report_type = "morning"
        user = get_user(user_id)
        stats = user.get('stats', {})
        stats['morning_checkin'] = stats.get('morning_checkin', 0) + 1
        save_user_field(user_id, 'stats', stats)
    elif any(w in lower for w in ["вечер", "evening", "итог", "сделал", "контролировал", "хозяин", "пожарный"]):
        report_type = "evening"
        user = get_user(user_id)
        stats = user.get('stats', {})
        stats['evening_checkin'] = stats.get('evening_checkin', 0) + 1
        save_user_field(user_id, 'stats', stats)
    save_report(user_id, report_type, text)
    profile = get_user_style(user_id)
    blocks = get_blocks_for_profile(profile)
    if report_type == "morning":
        reply_blocks = blocks.get("morning", ["N-4", "N-5"])
    elif report_type == "evening":
        reply_blocks = blocks.get("evening", ["N-1", "N-3"])
    else:
        reply_blocks = blocks.get("general", ["N-1"])
    user = get_user(user_id)
    reply = build_reply(reply_blocks, user_id, user.get('name', 'Армен'))
    send_keyboard(chat_id, reply, get_main_menu())

# ---------- ВЕБХУК ----------
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        return "Webhook работает!"
    try:
        data = request.get_json()
        logger.info(f"Получен запрос: {data}")
        if data and 'callback_query' in data:
            callback = data['callback_query']
            chat_id = callback['message']['chat']['id']
            user_id = callback['from']['id']
            username = callback['from'].get('username')
            callback_data = callback['data']
            callback_id = callback['id']
            user = get_or_create_user(user_id, username)

            # --- Карта архетипов ---
            if callback_data.startswith('map:'):
                idx_str = callback_data.split(':')[1]
                try:
                    idx = int(idx_str)
                except:
                    answer_callback(callback_id, "Ошибка")
                    return 'ok', 200
                if 'map_sessions' not in globals():
                    map_sessions = {}
                if user_id not in map_sessions:
                    map_sessions[user_id] = {"answers": {}, "step": 0}
                session = map_sessions[user_id]
                step = session["step"]
                if step < len(MAP_QUESTIONS):
                    q = MAP_QUESTIONS[step]
                    qid = q["id"]
                    options = [opt["label"] for opt in q["options"]]
                    if 0 <= idx < len(options):
                        session["answers"][qid] = options[idx]
                next_step = step + 1
                session["step"] = next_step
                if next_step < len(MAP_QUESTIONS):
                    q_text, options = get_question_text(next_step)
                    keyboard = get_map_keyboard(options)
                    text = f"🗺 Карта архетипов — вопрос {next_step+1} из {len(MAP_QUESTIONS)}\n\n{q_text}"
                    url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
                    payload = {
                        'chat_id': chat_id,
                        'message_id': callback['message']['message_id'],
                        'text': text,
                        'reply_markup': json.dumps(keyboard),
                        'parse_mode': 'Markdown'
                    }
                    requests.post(url, json=payload, timeout=5)
                    answer_callback(callback_id, "Выбор принят")
                else:
                    profile = calculate_profile(session["answers"])
                    save_user_field(user_id, 'archetype_profile', profile)
                    user_name = user.get('name', 'Армен')
                    text = build_profile_text(profile, user_name)
                    keyboard = get_map_done_keyboard()
                    url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
                    payload = {
                        'chat_id': chat_id,
                        'message_id': callback['message']['message_id'],
                        'text': text,
                        'reply_markup': json.dumps(keyboard),
                        'parse_mode': 'Markdown'
                    }
                    requests.post(url, json=payload, timeout=5)
                    answer_callback(callback_id, "Профиль сохранён")
                return 'ok', 200

            elif callback_data.startswith('map_done:'):
                action = callback_data.split(':')[1]
                if action == 'ok':
                    answer_callback(callback_id, "Профиль сохранён")
                    send_message(chat_id, "Меню восстановлено.", reply_markup=get_main_menu())
                elif action == 'retry':
                    if 'map_sessions' not in globals():
                        map_sessions = {}
                    map_sessions[user_id] = {"answers": {}, "step": 0}
                    q_text, options = get_question_text(0)
                    keyboard = get_map_keyboard(options)
                    text = f"🗺 Карта архетипов — вопрос 1 из {len(MAP_QUESTIONS)}\n\n{q_text}"
                    url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
                    payload = {
                        'chat_id': chat_id,
                        'message_id': callback['message']['message_id'],
                        'text': text,
                        'reply_markup': json.dumps(keyboard),
                        'parse_mode': 'Markdown'
                    }
                    requests.post(url, json=payload, timeout=5)
                    answer_callback(callback_id, "Перезапуск карты")
                return 'ok', 200

            elif callback_data == "main_menu":
                answer_callback(callback_id, "Главное меню")
                send_keyboard(chat_id, "Главное меню. Выберите действие:", get_main_menu())
                url = f"https://api.telegram.org/bot{TOKEN}/editMessageReplyMarkup"
                payload = {'chat_id': chat_id, 'message_id': callback['message']['message_id'], 'reply_markup': json.dumps({})}
                try:
                    requests.post(url, json=payload, timeout=5)
                except:
                    pass
                return 'ok', 200

            elif callback_data.startswith("practices_toggle:"):
                show_mode = callback_data.split(":")[1]
                show_all = (show_mode == "all")
                if 'show_all_state' not in globals():
                    show_all_state = {}
                show_all_state[user_id] = show_all
                practices = PRACTICES
                progress = get_user_progress(user_id)
                todo_count = sum(1 for p in practices if not is_practice_done_today(progress, p["id"]))
                header = "🧘 *Практики*\n\n"
                if todo_count > 0 and not show_all:
                    header += f"Осталось на сегодня: *{todo_count}*\n\n"
                else:
                    header += f"Всего практик: *{len(practices)}*\n\n"
                text = header + "Выберите практику:"
                keyboard = get_practices_list_keyboard(user_id, show_all)
                url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
                payload = {
                    'chat_id': chat_id,
                    'message_id': callback['message']['message_id'],
                    'text': text,
                    'reply_markup': json.dumps(keyboard),
                    'parse_mode': 'Markdown'
                }
                requests.post(url, json=payload, timeout=5)
                answer_callback(callback_id, "Обновлено")
                return 'ok', 200

            elif callback_data == "practices_list":
                show_all = False
                if 'show_all_state' in globals() and user_id in show_all_state:
                    show_all = show_all_state[user_id]
                practices = PRACTICES
                progress = get_user_progress(user_id)
                todo_count = sum(1 for p in practices if not is_practice_done_today(progress, p["id"]))
                header = "🧘 *Практики*\n\n"
                if todo_count > 0 and not show_all:
                    header += f"Осталось на сегодня: *{todo_count}*\n\n"
                else:
                    header += f"Всего практик: *{len(practices)}*\n\n"
                text = header + "Выберите практику:"
                keyboard = get_practices_list_keyboard(user_id, show_all)
                url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
                payload = {
                    'chat_id': chat_id,
                    'message_id': callback['message']['message_id'],
                    'text': text,
                    'reply_markup': json.dumps(keyboard),
                    'parse_mode': 'Markdown'
                }
                requests.post(url, json=payload, timeout=5)
                answer_callback(callback_id, "Список практик")
                return 'ok', 200

            elif callback_data.startswith("practice_view:"):
                pid = callback_data.split(":")[1]
                practice = get_practice_by_id(pid)
                if not practice:
                    answer_callback(callback_id, "Практика не найдена")
                    return 'ok', 200
                progress = get_user_progress(user_id)
                prog = progress.get(pid, {})
                completed_count = prog.get("completed_count", 0)
                streak = prog.get("streak", 0)
                completed_today = is_practice_done_today(progress, pid)
                status_emoji = "✅" if completed_today else "⬜"
                text = (
                    f"{status_emoji} *{practice['name']}*\n"
                    f"_{practice['when']}_ | {practice['duration']}\n\n"
                    f"{practice['text']}\n\n"
                    f"📊 Статистика:\n"
                    f"• Выполнено всего: {completed_count}\n"
                    f"• Серия (streak): {streak}\n"
                )
                if prog.get("last_used"):
                    text += f"• Последний раз: {prog['last_used'].split('T')[0]}\n"
                keyboard = get_practice_detail_keyboard(pid, completed_today)
                url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
                payload = {
                    'chat_id': chat_id,
                    'message_id': callback['message']['message_id'],
                    'text': text,
                    'reply_markup': json.dumps(keyboard),
                    'parse_mode': 'Markdown'
                }
                requests.post(url, json=payload, timeout=5)
                answer_callback(callback_id, "Подробнее")
                return 'ok', 200

            elif callback_data.startswith("practice_done:"):
                pid = callback_data.split(":")[1]
                mark_practice_done(user_id, pid)
                answer_callback(callback_id, "✅ Отмечено!")
                practice = get_practice_by_id(pid)
                progress = get_user_progress(user_id)
                prog = progress.get(pid, {})
                completed_count = prog.get("completed_count", 0)
                streak = prog.get("streak", 0)
                completed_today = True
                status_emoji = "✅"
                text = (
                    f"{status_emoji} *{practice['name']}* — выполнено!\n\n"
                    f"{practice['text']}\n\n"
                    f"📊 Статистика:\n"
                    f"• Выполнено всего: {completed_count}\n"
                    f"• Серия (streak): {streak}\n"
                    f"• Последний раз: сегодня\n"
                )
                keyboard = get_practice_detail_keyboard(pid, completed_today)
                url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
                payload = {
                    'chat_id': chat_id,
                    'message_id': callback['message']['message_id'],
                    'text': text,
                    'reply_markup': json.dumps(keyboard),
                    'parse_mode': 'Markdown'
                }
                requests.post(url, json=payload, timeout=5)
                return 'ok', 200

            elif callback_data.startswith("practice_undo:"):
                pid = callback_data.split(":")[1]
                undo_practice_done(user_id, pid)
                answer_callback(callback_id, "Отменено")
                practice = get_practice_by_id(pid)
                progress = get_user_progress(user_id)
                prog = progress.get(pid, {})
                completed_count = prog.get("completed_count", 0)
                streak = prog.get("streak", 0)
                completed_today = False
                status_emoji = "⬜"
                text = (
                    f"{status_emoji} *{practice['name']}* — отменено\n\n"
                    f"{practice['text']}\n\n"
                    f"📊 Статистика:\n"
                    f"• Выполнено всего: {completed_count}\n"
                    f"• Серия (streak): {streak}\n"
                )
                keyboard = get_practice_detail_keyboard(pid, completed_today)
                url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
                payload = {
                    'chat_id': chat_id,
                    'message_id': callback['message']['message_id'],
                    'text': text,
                    'reply_markup': json.dumps(keyboard),
                    'parse_mode': 'Markdown'
                }
                requests.post(url, json=payload, timeout=5)
                return 'ok', 200

            elif callback_data == "noop":
                answer_callback(callback_id)
                return 'ok', 200

            answer_callback(callback_id, "Команда выполнена")
            return 'ok', 200

        if data and 'message' in data:
            msg = data['message']
            chat_id = msg['chat']['id']
            user_id = msg['from']['id']
            username = msg['from'].get('username')
            text = msg.get('text', '')
            voice = msg.get('voice')
            if voice:
                send_message(chat_id, "Я пока не умею читать голосовые. Напиши текст 📝")
                return 'ok', 200
            if not text or text.strip() == '':
                send_message(chat_id, "Я не понимаю пустые сообщения. Напиши текст или выбери кнопку.")
                return 'ok', 200
            user = get_or_create_user(user_id, username)

            paused = user.get('paused', False)
            if paused and text not in ["▶️ Возобновить", "/resume"]:
                send_keyboard(chat_id, "Программа на паузе. Нажми «▶️ Возобновить».", get_resume_menu())
                return 'ok', 200

            if text.startswith('/'):
                if text == '/start':
                    handle_start(chat_id, user_id, username)
                elif text == '/today':
                    handle_today(chat_id, user_id)
                elif text == '/stats':
                    handle_stats(chat_id, user_id)
                elif text == '/practices':
                    handle_practices(chat_id, user_id)
                elif text == '/style':
                    handle_style(chat_id, user_id)
                elif text == '/pause':
                    handle_pause(chat_id, user_id)
                elif text == '/resume':
                    handle_resume(chat_id, user_id)
                elif text == '/help':
                    handle_help(chat_id)
                else:
                    send_keyboard(chat_id, "Неизвестная команда. Используй кнопки меню.", get_main_menu())
                return 'ok', 200

            if text == "📋 Сегодня":
                handle_today(chat_id, user_id)
            elif text == "📊 Статистика":
                handle_stats(chat_id, user_id)
            elif text == "🧘 Практики":
                handle_practices(chat_id, user_id)
            elif text == "🎯 Стиль":
                handle_style(chat_id, user_id)
            elif text == "⏸ Пауза":
                handle_pause(chat_id, user_id)
            elif text == "▶️ Возобновить":
                handle_resume(chat_id, user_id)
            elif text == "❓ Помощь":
                handle_help(chat_id)
            elif text == "📝 Отчёт готов":
                send_message(chat_id, "Напиши три строки:\n1. Что я контролировал сегодня?\n2. Был хозяином дня или пожарным?\n3. Что оставляю за дверью?\n\nИли просто напиши свои мысли — я услышу.")
            else:
                handle_report(chat_id, user_id, text)
        return 'ok', 200
    except Exception as e:
        logger.error(f"Ошибка в webhook: {e}")
        return 'ok', 200

# ---------- ПЛАНИРОВЩИК ----------
scheduler = BackgroundScheduler(timezone=pytz.timezone(TIMEZONE))
scheduler.start()

def send_practice_reminder(practice_id):
    try:
        user = get_user(USER_ID)
        if user is None or user.get('paused', False):
            return
        practice = get_practice_by_id(practice_id)
        if not practice:
            return
        progress = get_user_progress(USER_ID)
        if is_practice_done_today(progress, practice_id):
            return
        text = (
            f"🧘 *{practice['name']}*\n\n"
            f"_{practice['when']}_ | {practice['duration']}\n\n"
            f"{practice['text']}\n\n"
            f"Нажми «✅ Сделано» или открой через «🧘 Практики»"
        )
        keyboard = {
            'inline_keyboard': [
                [{'text': "✅ Сделано", 'callback_data': f"practice_done:{practice_id}"}],
                [{'text': "📖 Подробнее", 'callback_data': f"practice_view:{practice_id}"}],
            ]
        }
        send_keyboard(USER_ID, text, keyboard, parse_mode='Markdown')
        logger.info(f"Пуш практики {practice_id} отправлен")
    except Exception as e:
        logger.error(f"Ошибка в send_practice_reminder({practice_id}): {e}")

def scheduled_morning():
    try:
        user = get_user(USER_ID)
        if user is None or user.get('paused', False):
            return
        chat_id = USER_ID
        handle_today(chat_id, USER_ID)
    except Exception as e:
        logger.error(f"Ошибка в scheduled_morning: {e}")

def scheduled_evening():
    try:
        user = get_user(USER_ID)
        if user is None or user.get('paused', False):
            return
        chat_id = USER_ID
        reports = get_reports(USER_ID, 1)
        has_report = False
        today = datetime.now().date().isoformat()
        for r in reports:
            if r['timestamp'].startswith(today) and r['report_type'] == 'evening':
                has_report = True
                break
        profile = get_user_style(USER_ID)
        blocks = get_blocks_for_profile(profile)
        evening_blocks = blocks.get("evening", ["N-6", "N-7"])
        reply = build_reply(evening_blocks, USER_ID, user.get('name', 'Армен'))
        schedule = get_today_schedule()
        evening_practices = schedule.get('evening', [])
        practices_text = ""
        if evening_practices:
            practices_text = "\n\n📋 Вечерние практики:\n"
            for p in evening_practices:
                if p['key'] != 'вечерний_мини_отчёт':
                    practices_text += f"• {p['text']}\n"
        if not has_report:
            text = (
                f"🌙 Вечер, Армен.\n\n"
                f"🎯 Подведение итогов:\n{reply}"
                f"{practices_text}\n\n"
                f"📝 Отчёт ещё не сделан. Нажми «📝 Отчёт готов» или просто напиши три строки."
            )
            send_keyboard(chat_id, text, get_report_menu())
        else:
            text = f"🌙 Вечер, Армен.\n\n🎯 {reply}{practices_text}\n\nОтчёт уже принят. Хорошего вечера."
            send_keyboard(chat_id, text, get_main_menu())
    except Exception as e:
        logger.error(f"Ошибка в scheduled_evening: {e}")

def setup_scheduler():
    morning_time = get_user_setting(USER_ID, 'morning_time', '06:30')
    h, m = morning_time.split(':')
    scheduler.add_job(
        scheduled_morning,
        CronTrigger(hour=int(h), minute=int(m)),
        id='morning_job',
        replace_existing=True
    )
    evening_time = get_user_setting(USER_ID, 'evening_time', '23:00')
    h, m = evening_time.split(':')
    scheduler.add_job(
        scheduled_evening,
        CronTrigger(hour=int(h), minute=int(m)),
        id='evening_job',
        replace_existing=True
    )
    for p in PRACTICES:
        pid = p['id']
        if pid == 'P-3':
            continue
        schedule_time = p.get('schedule_time', '06:30')
        days = p.get('schedule_days', [])
        for day in days:
            h, m = schedule_time.split(':')
            job_id = f"practice_{pid}_day{day}"
            scheduler.add_job(
                send_practice_reminder,
                CronTrigger(day_of_week=str(day), hour=int(h), minute=int(m)),
                args=[pid],
                id=job_id,
                replace_existing=True
            )
    scheduler.add_job(
        send_practice_reminder,
        CronTrigger(day_of_week='0-6', hour=10, minute=30),
        args=['P-3'],
        id='practice_P-3_daily',
        replace_existing=True
    )
    logger.info("Планировщик настроен.")

# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    setup_scheduler()
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
