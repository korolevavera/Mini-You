# -*- coding: utf-8 -*-
import os
import json
import logging
import re
import time
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

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set")

TIMEZONE = os.environ.get('TIMEZONE', 'Europe/Moscow')
USER_ID = int(os.environ.get('USER_ID', 0))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- БАЗА ДАННЫХ ----------
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
                evening_time TEXT DEFAULT '23:00'
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    conn.close()

# ИНИЦИАЛИЗАЦИЯ ПРИ ЗАПУСКЕ
init_db()

# ---------- АРХЕТИПЫ ----------
ARCHETYPES = {
    "Дирижёр": {"ennea": "1+8", "desire": "Быть правым, сильным", "strength": "Самоуправление, лидерство", "shadow": "Переконтроль, перфекционизм", "rule": "Контролируй то, что усиливает жизнь."},
    "Искатель": {"ennea": "5+7", "desire": "Быть компетентным, свободным", "strength": "Исследование, адаптация", "shadow": "Незавершённость", "rule": "Путь — не оправдание бегства."},
    "Маг": {"ennea": "5+1", "desire": "Быть компетентным, обладающим ключом", "strength": "Аналитика, трансформация", "shadow": "Изоляция", "rule": "Знание — ответственность делиться."},
    "Простодушный": {"ennea": "9+2", "desire": "Быть в гармонии", "strength": "Создание комфорта", "shadow": "Пассивность", "rule": "Доверие — не отказ от выбора."},
    "Любовник": {"ennea": "4+2", "desire": "Быть уникальным", "strength": "Эмпатия, присутствие", "shadow": "Зависимость от связи", "rule": "Связь — не слияние."},
    "Правитель": {"ennea": "8+1", "desire": "Быть сильным, защищённым", "strength": "Создание систем", "shadow": "Ригидность", "rule": "Власть — служение."},
    "Мудрец": {"ennea": "5+9", "desire": "Быть компетентным, целым", "strength": "Аналитика, философия", "shadow": "Отстранённость", "rule": "Знание без действия — бесплодно."},
    "Воин": {"ennea": "8+6", "desire": "Быть сильным, в безопасности", "strength": "Защита слабых, дисциплина", "shadow": "Гипер-независимость", "rule": "Просить помощь — не слабость."},
    "Заботливый": {"ennea": "2+9", "desire": "Быть нужным", "strength": "Терапия, забота", "shadow": "Жертва как идентичность", "rule": "Ты не обязан питать всех."},
    "Герой": {"ennea": "8+3", "desire": "Быть сильным, значимым", "strength": "Активизм, вдохновение", "shadow": "Спасательство", "rule": "Не каждый кризис — твой."},
    "Бунтарь": {"ennea": "8+4", "desire": "Быть свободным", "strength": "Честность, освобождение", "shadow": "Разрушение без создания", "rule": "Разрушай, но предлагай."},
    "Странник": {"ennea": "5+4", "desire": "Быть свободным, компетентным", "strength": "Автономия, творчество", "shadow": "Отчуждение", "rule": "Дистанция — не стена."},
    "Шут": {"ennea": "7+2", "desire": "Быть довольным, свободным", "strength": "Разряжение напряжения", "shadow": "Ирония как бегство", "rule": "Юмор — не отрицание."},
    "Учитель": {"ennea": "2+5", "desire": "Быть нужным, полезным", "strength": "Образование, коучинг", "shadow": "Нужда в учениках", "rule": "Ты тоже ученик."},
    "Дипломат": {"ennea": "9+6", "desire": "Быть в гармонии", "strength": "Медиация, перевод", "shadow": "Потеря себя в балансе", "rule": "Не каждый конфликт нужно решать."},
}

METAPHORS = {
    "Дирижёр": "оркестр", "Искатель": "путь", "Маг": "мост", "Простодушный": "тёплый очаг",
    "Любовник": "связь", "Правитель": "крепость", "Мудрец": "светильник", "Воин": "щит",
    "Заботливый": "сад", "Герой": "огонь", "Бунтарь": "ветер", "Странник": "горизонт",
    "Шут": "зеркало", "Учитель": "мост знаний", "Дипломат": "перевод"
}

AFFIRMATIONS = {
    "Дирижёр": "Я легко беру контроль там, где это приносит пользу.",
    "Искатель": "Я — путь. Каждый шаг — это уже прибытие.",
    "Маг": "Я — мост. Я соединяю то, что казалось разделённым.",
    "Простодушный": "Я — тепло. Я позволяю миру быть мягким.",
    "Любовник": "Я — связь. Я вижу красоту в обыденности.",
    "Правитель": "Я — опора. Я создаю пространство для роста.",
    "Мудрец": "Я — свет. Я вижу то, что скрыто.",
    "Воин": "Я — щит. Я защищаю то, что важно.",
    "Заботливый": "Я — сад. Я даю рост другим, но и сам расту.",
    "Герой": "Я — огонь. Я горю, но не сгораю.",
    "Бунтарь": "Я — ветер. Я сдуваю мёртвое для живого.",
    "Странник": "Я — горизонт. Я вижу дальше.",
    "Шут": "Я — зеркало. Я отражаю абсурд.",
    "Учитель": "Я — мост между знанием и действием.",
    "Дипломат": "Я — перевод. Я нахожу общий язык."
}

MAP_QUESTIONS = [
    {
        "id": "crisis",
        "text": "Когда всё рушится, твой первый импульс?",
        "options": [
            {"label": "Взять контроль. Восстановить порядок.", "archetypes": ["Дирижёр", "Правитель"]},
            {"label": "Найти выход. Построить мост.", "archetypes": ["Искатель", "Дипломат"]},
            {"label": "Уйти. Наблюдать. Понять.", "archetypes": ["Маг", "Мудрец", "Странник"]},
            {"label": "Защитить тех, кто слабее.", "archetypes": ["Воин", "Герой"]},
            {"label": "Создать комфорт. Сохранить тепло.", "archetypes": ["Простодушный", "Заботливый"]},
            {"label": "Показать абсурд. Разрядить.", "archetypes": ["Шут", "Бунтарь"]},
            {"label": "Углубиться в чувство.", "archetypes": ["Любовник"]},
            {"label": "Найти, чему научиться.", "archetypes": ["Учитель"]},
        ]
    },
    {
        "id": "home",
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
        ]
    },
    {
        "id": "shadow",
        "text": "Твоя тень — что ты скрываешь даже от себя?",
        "options": [
            {"label": "Я бегу, прежде чем останусь.", "archetypes": ["Искатель", "Странник"]},
            {"label": "Я контролирую, потому что боюсь хаоса внутри.", "archetypes": ["Дирижёр", "Правитель"]},
            {"label": "Я знаю всё, но не действую.", "archetypes": ["Маг", "Мудрец"]},
            {"label": "Я отдаю, чтобы не чувствовать пустоту.", "archetypes": ["Заботливый", "Учитель"]},
            {"label": "Я сглаживаю, чтобы не выбирать.", "archetypes": ["Дипломат", "Простодушный"]},
            {"label": "Я смеюсь, чтобы не плакать.", "archetypes": ["Шут", "Любовник"]},
            {"label": "Я спасаю, чтобы не быть обычным.", "archetypes": ["Герой", "Воин"]},
            {"label": "Я ломаю, прежде чем построю.", "archetypes": ["Бунтарь", "Воин"]},
        ]
    }
]

# ---------- ПРАКТИКИ ----------
PRACTICES = [
    {"id": "P-1", "name": "Дыхание", "category": "morning", "when": "Утро", "duration": "3 мин",
     "text": "Сядь прямо. Сделай 5 глубоких вдохов. На выдохе представляй, как уходит напряжение."},
    {"id": "P-2", "name": "Утренняя установка", "category": "morning", "when": "Утро", "duration": "2 мин",
     "text": "Спроси себя: что я хочу увидеть вечером? Запиши одну мысль."},
    {"id": "P-3", "name": "Аффирмация", "category": "morning", "when": "Утро", "duration": "1 мин",
     "text": "Прочти аффирмацию. Просто прочти. Не обязан отвечать."},
    {"id": "P-4", "name": "Вечерний мини-отчёт", "category": "evening", "when": "Вечер", "duration": "5 мин",
     "text": "Напиши три строки:\n1. Что я контролировал сегодня?\n2. Был хозяином дня или пожарным?\n3. Что оставляю за дверью?"},
]

BLOCKS = [
    {"id": "N-1", "text": "Твоё Второе Я — единственный на сцене, кто держит тишину между нотами."},
    {"id": "N-2", "text": "Ты не должен быть всем — ты должен быть собой. Это уже достаточно."},
    {"id": "N-3", "text": "Позволь себе быть несовершенным сегодня. Это не поражение, это дыхание."},
    {"id": "N-4", "text": "Ты — {metaphor}. Ты не боишься хаоса, ты знаешь, что из него рождается порядок."},
    {"id": "N-5", "text": "Твоя сила — {core}. Твоя тень — {shadow}. Интеграция — это когда ты позволяешь им быть."},
    {"id": "N-6", "text": "Сегодня ты был(а) хозяином дня. Завтра тоже будешь."},
    {"id": "N-7", "text": "Оставь за дверью то, что не служит твоему росту."},
    {"id": "N-8", "text": "Ты — путь. Каждый шаг — уже прибытие."},
    {"id": "N-9", "text": "Мудрость — не в том, чтобы знать всё, а в том, чтобы быть с тем, что есть."},
    {"id": "N-10", "text": "Ты — огонь. Ты горишь, но не сгораешь."},
]

# ---------- ФУНКЦИИ ----------
def get_user(user_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    conn.close()
    if row:
        row['archetype_profile'] = json.loads(row['archetype_profile'] or '{}')
        row['practice_progress'] = json.loads(row['practice_progress'] or '{}')
        row['stats'] = json.loads(row['stats'] or '{}')
    return row

def save_user_field(user_id, field, value):
    conn = get_db_connection()
    with conn.cursor() as cur:
        if isinstance(value, (list, dict)):
            value = json.dumps(value)
        cur.execute(f"UPDATE users SET {field} = %s WHERE user_id = %s", (value, user_id))
        conn.commit()
    conn.close()

def get_or_create_user(user_id, username=None, name=None):
    user = get_user(user_id)
    if not user:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (user_id, username, name) VALUES (%s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
                (user_id, username, name or 'Армен')
            )
            conn.commit()
        conn.close()
        user = get_user(user_id)
    return user

def calculate_profile(answers):
    scores = {name: 0 for name in ARCHETYPES}
    for q in MAP_QUESTIONS:
        qid = q["id"]
        selected = answers.get(qid)
        if not selected:
            continue
        for opt in q["options"]:
            if opt["label"] == selected:
                for arch in opt["archetypes"]:
                    scores[arch] += 1
                break
    sorted_arch = sorted(scores.items(), key=lambda x: -x[1])
    core = sorted_arch[0][0]
    support = sorted_arch[1][0] if len(sorted_arch) > 1 else core
    shadow = sorted_arch[-1][0] if sorted_arch[-1][0] != core else sorted_arch[-2][0]
    return {"core": core, "support": support, "shadow": shadow, "metaphor": METAPHORS.get(core, "оркестр")}

def get_today_schedule():
    weekday = datetime.now().weekday()
    morning = [p for p in PRACTICES if p["category"] == "morning"]
    evening = [p for p in PRACTICES if p["category"] == "evening"]
    return {"morning": morning, "evening": evening}

def get_daily_task():
    tasks = [
        "Завтра спроси себя: что я хочу увидеть вечером?",
        "Найди дело, которое можно сделать на 70%, и остановись.",
        "Запиши: где сегодня я был хозяином дня, а где — пожарным?",
        "Попроси помощь в одном деле.",
        "Найди дело, которое тянешь. Поставь точку остановки.",
        "Сделай что-то без плана.",
        "Выбери дело, которое доводишь до идеала. Сделай на 90%.",
    ]
    return tasks[datetime.now().weekday() % len(tasks)]

def get_affirmation(core):
    return AFFIRMATIONS.get(core, "Ты на правильном пути.")

def build_reply(block_ids, user_id):
    user = get_user(user_id)
    profile = user.get('archetype_profile', {}) if user else {}
    core = profile.get('core', 'Дирижёр')
    shadow = profile.get('shadow', 'Простодушный')
    metaphor = profile.get('metaphor', 'оркестр')
    parts = []
    for bid in block_ids:
        block = next((b for b in BLOCKS if b['id'] == bid), {})
        text = block.get('text', '')
        text = text.replace('{core}', core).replace('{shadow}', shadow).replace('{metaphor}', metaphor)
        parts.append(text)
    return '\n\n'.join(parts)

# ---------- ОТПРАВКА СООБЩЕНИЙ ----------
def send_message(chat_id, text, keyboard=None):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
    if keyboard:
        payload['reply_markup'] = json.dumps(keyboard)
    try:
        return requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Send error: {e}")

def send_keyboard(chat_id, text, keyboard):
    return send_message(chat_id, text, keyboard)

def answer_callback(callback_id, text=''):
    url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={'callback_query_id': callback_id, 'text': text}, timeout=5)
    except:
        pass

# ---------- КЛАВИАТУРЫ ----------
def get_main_menu():
    keyboard = [
        [{"text": "📋 Сегодня"}, {"text": "📊 Статистика"}],
        [{"text": "🧘 Практики"}, {"text": "🎯 Стиль"}],
        [{"text": "📖 История"}, {"text": "⏸ Пауза"}],
        [{"text": "❓ Помощь"}],
    ]
    return {'keyboard': keyboard, 'resize_keyboard': True}

def get_map_keyboard(options):
    buttons = [[{"text": opt, "callback_data": f"map:{idx}"}] for idx, opt in enumerate(options)]
    return {'inline_keyboard': buttons}

def get_practice_keyboard(practices, user_id):
    progress = get_user(user_id)['practice_progress'] if get_user(user_id) else {}
    keyboard = []
    for p in practices:
        done = False
        if p['id'] in progress:
            last = progress[p['id']].get('last_used', '')
            done = last.startswith(datetime.now().date().isoformat())
        status = "✅" if done else "⬜"
        keyboard.append([{"text": f"{status} {p['name']}", "callback_data": f"practice_view:{p['id']}"}])
    return {'inline_keyboard': keyboard}

# ---------- ОБРАБОТЧИКИ ----------
def handle_history(chat_id, user_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT message, timestamp FROM user_messages WHERE user_id = %s ORDER BY timestamp DESC LIMIT 50", (user_id,))
        rows = cur.fetchall()
    conn.close()

    if not rows:
        send_message(chat_id, "📖 История пуста. Напиши что-нибудь, и я сохраню.")
        send_keyboard(chat_id, "Главное меню:", get_main_menu())
def handle_start(chat_id, user_id):
    user = get_or_create_user(user_id)
    name = user.get('name', 'Армен')
    text = f"Привет, {name}!\n\nЯ — твое Второе Я. Я здесь, чтобы помочь тебе следить за ритмом.\n\nИспользуй кнопки ниже 👇"
    send_keyboard(chat_id, text, get_main_menu())

def handle_today(chat_id, user_id):
    user = get_or_create_user(user_id)
    profile = user.get('archetype_profile', {})
    core = profile.get('core', 'Дирижёр')
    aff = get_affirmation(core)
    blocks = ["N-4", "N-5"]
    reply = build_reply(blocks, user_id)
    task = get_daily_task()
    schedule = get_today_schedule()
    schedule_text = "\n\n🌅 Утро:\n" + "\n".join(f"• {p['text']}" for p in schedule.get('morning', []))
    schedule_text += "\n\n🌙 Вечер:\n" + "\n".join(f"• {p['text']}" for p in schedule.get('evening', []))
    text = f"🌅 Доброе утро!\n\n💫 Аффирмация:\n{aff}\n\n🎯 Настройка:\n{reply}\n\n❗ Задание:\n{task}{schedule_text}"
    send_keyboard(chat_id, text, get_main_menu())

def handle_stats(chat_id, user_id):
    user = get_user(user_id)
    if not user:
        user = get_or_create_user(user_id)
    profile = user.get('archetype_profile', {})
    core = profile.get('core', '—')
    shadow = profile.get('shadow', '—')
    progress = user.get('practice_progress', {})
    total = sum(p.get('completed_count', 0) for p in progress.values())
    text = f"📊 Статистика\n\nЯдро: {core}\nТень: {shadow}\nМетафора: {METAPHORS.get(core, '—')}\n\n🧘 Практик выполнено: {total}"
    send_keyboard(chat_id, text, get_main_menu())

def handle_practices(chat_id, user_id):
    user = get_or_create_user(user_id)
    practices = PRACTICES
    keyboard = get_practice_keyboard(practices, user_id)
    text = "🧘 Практики\n\nВыбери практику:"
    send_keyboard(chat_id, text, keyboard)

def handle_style(chat_id, user_id):
    if 'map_sessions' not in globals():
        global map_sessions
        map_sessions = {}
    map_sessions[user_id] = {"answers": {}, "step": 0}
    q = MAP_QUESTIONS[0]
    text = f"🗺 Карта архетипов — вопрос 1 из {len(MAP_QUESTIONS)}\n\n{q['text']}"
    keyboard = get_map_keyboard([opt['label'] for opt in q['options']])
    send_keyboard(chat_id, text, keyboard)

def handle_pause(chat_id, user_id):
    save_user_field(user_id, 'paused', True)
    keyboard = [{"text": "▶️ Возобновить"}]
    send_keyboard(chat_id, "Программа приостановлена.", {'keyboard': keyboard, 'resize_keyboard': True})

def handle_resume(chat_id, user_id):
    save_user_field(user_id, 'paused', False)
    send_keyboard(chat_id, "Программа возобновлена.", get_main_menu())

def handle_help(chat_id):
    text = "📖 Помощь\n\n📋 Сегодня — расписание\n📊 Статистика — твои данные\n🧘 Практики — список практик\n🎯 Стиль — карта архетипов\n⏸ Пауза — остановить\n▶️ Возобновить — продолжить"
    send_keyboard(chat_id, text, get_main_menu())
    
    return

    history_text = "📖 *Твоя полная история:*\n\n"
    for row in rows:
        date = row['timestamp'].strftime('%d.%m.%Y %H:%M')
        history_text += f"*{date}*\n{row['message']}\n\n"
        history_text += "—" * 30 + "\n\n"
    
    if len(history_text) > 4000:
        parts = []
        current_part = ""
        for row in rows:
            date = row['timestamp'].strftime('%d.%m.%Y %H:%M')
            block = f"*{date}*\n{row['message']}\n\n" + "—" * 30 + "\n\n"
            if len(current_part) + len(block) > 4000:
                parts.append(current_part)
                current_part = block
            else:
                current_part += block
        if current_part:
            parts.append(current_part)
        
        for i, part in enumerate(parts):
            if i == 0:
                send_message(chat_id, f"📖 *Твоя история (часть {i+1}/{len(parts)}):*\n\n{part}")
            else:
                send_message(chat_id, f"📖 *Продолжение (часть {i+1}/{len(parts)}):*\n\n{part}")
    else:
        send_message(chat_id, history_text)
    
    send_keyboard(chat_id, "Главное меню:", get_main_menu())

# ---------- ВЕБХУК ----------
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        logger.info(f"Получен запрос: {data}")

        if 'callback_query' in data:
            cb = data['callback_query']
            chat_id = cb['message']['chat']['id']
            user_id = cb['from']['id']
            username = cb['from'].get('username')
            callback_data = cb['data']
            callback_id = cb['id']

            get_or_create_user(user_id, username)

            if callback_data.startswith('map:'):
                idx = int(callback_data.split(':')[1])
                if 'map_sessions' not in globals():
                    map_sessions = {}
                session = map_sessions.get(user_id, {"answers": {}, "step": 0})
                step = session['step']
                if step < len(MAP_QUESTIONS):
                    q = MAP_QUESTIONS[step]
                    options = [opt['label'] for opt in q['options']]
                    if 0 <= idx < len(options):
                        session['answers'][q['id']] = options[idx]
                next_step = step + 1
                session['step'] = next_step
                map_sessions[user_id] = session

                if next_step < len(MAP_QUESTIONS):
                    q = MAP_QUESTIONS[next_step]
                    text = f"🗺 Карта архетипов — вопрос {next_step+1} из {len(MAP_QUESTIONS)}\n\n{q['text']}"
                    keyboard = get_map_keyboard([opt['label'] for opt in q['options']])
                    url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
                    payload = {
                        'chat_id': chat_id,
                        'message_id': cb['message']['message_id'],
                        'text': text,
                        'reply_markup': json.dumps(keyboard)
                    }
                    requests.post(url, json=payload, timeout=5)
                    answer_callback(callback_id, "Выбор принят")
                else:
                    profile = calculate_profile(session['answers'])
                    save_user_field(user_id, 'archetype_profile', profile)
                    user = get_user(user_id)
                    name = user.get('name', 'Армен')
                    text = f"🎯 Профиль построен, {name}!\n\n🔥 Ядро: {profile['core']}\n🛡️ Опора: {profile['support']}\n🌑 Тень: {profile['shadow']}\n\nТеперь я буду говорить с тобой на языке {profile['core']}."
                    keyboard = {'inline_keyboard': [[{"text": "✅ Всё верно", "callback_data": "map_done"}]]}
                    url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
                    payload = {
                        'chat_id': chat_id,
                        'message_id': cb['message']['message_id'],
                        'text': text,
                        'reply_markup': json.dumps(keyboard)
                    }
                    requests.post(url, json=payload, timeout=5)
                    answer_callback(callback_id, "Профиль сохранён")
                return 'ok', 200

            if callback_data == "map_done":
                send_message(chat_id, "Отлично! Начни с «📋 Сегодня».")
                send_keyboard(chat_id, "Главное меню:", get_main_menu())
                answer_callback(callback_id, "Готово")
                return 'ok', 200

            # Практики
            if callback_data.startswith('practice_view:'):
                pid = callback_data.split(':')[1]
                practice = next((p for p in PRACTICES if p['id'] == pid), None)
                if practice:
                    text = f"🧘 *{practice['name']}*\n\n{practice['text']}\n\n_{practice['when']}_ | {practice['duration']}"
                    keyboard = {'inline_keyboard': [[{"text": "✅ Отметить выполненной", "callback_data": f"practice_done:{pid}"}]]}
                    send_keyboard(chat_id, text, keyboard)
                answer_callback(callback_id)
                return 'ok', 200

            if callback_data.startswith('practice_done:'):
                pid = callback_data.split(':')[1]
                practice = next((p for p in PRACTICES if p['id'] == pid), None)
                if practice:
                    progress = get_user(user_id)['practice_progress']
                    now = datetime.now().isoformat()
                    if pid not in progress:
                        progress[pid] = {"completed_count": 0, "last_used": None}
                    progress[pid]['completed_count'] = progress[pid].get('completed_count', 0) + 1
                    progress[pid]['last_used'] = now
                    save_user_field(user_id, 'practice_progress', progress)
                    send_message(chat_id, f"✅ {practice['name']} выполнена! Отлично!")
                answer_callback(callback_id, "Отмечено!")
                return 'ok', 200

            return 'ok', 200

        if 'message' in data:
            msg = data['message']
            chat_id = msg['chat']['id']
            user_id = msg['from']['id']
            username = msg['from'].get('username')
            text = msg.get('text', '')

            if not text:
                return 'ok', 200

            get_or_create_user(user_id, username)

            # Проверка паузы
            user = get_user(user_id)
            if user and user.get('paused', False) and text not in ["▶️ Возобновить"]:
                send_message(chat_id, "Программа на паузе. Нажми «▶️ Возобновить».")
                return 'ok', 200

            if text.startswith('/'):
                if text == '/start':
                    handle_start(chat_id, user_id)
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
            elif text == "📖 История":
                handle_history(chat_id, user_id)
            elif text == "▶️ Возобновить":
                handle_resume(chat_id, user_id)
            elif text == "❓ Помощь":
                handle_help(chat_id)
            else:
                # Сохраняем сообщение в БД
                conn = get_db_connection()
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO user_messages (user_id, message) VALUES (%s, %s)", (user_id, text))
                    conn.commit()
                conn.close()
                
                # Сохраняем статистику
                save_user_field(user_id, 'stats', {**user.get('stats', {}), 'reports': user.get('stats', {}).get('reports', 0) + 1})
                send_message(chat_id, "📝 Сохранил твои мысли. Спасибо!")
                send_keyboard(chat_id, "Главное меню:", get_main_menu())

        return 'ok', 200
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return 'ok', 200

# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
