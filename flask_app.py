# -*- coding: utf-8 -*-
import os
import json
import logging
import re
import time
import random
from datetime import datetime, timedelta, date
from flask import Flask, request
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set")

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set")

CRON_KEY = os.environ.get('CRON_KEY', 'my_secret_key_123')

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get('TIMEZONE', 'Europe/Moscow'))
except ImportError:
    TZ = None
    logging.warning("zoneinfo not available, using UTC")

logging.basicConfig(level=logging.INFO)

# ---------- БАЗА ДАННЫХ (PostgreSQL) ----------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    with conn.cursor() as cur:
        # Таблица users (уже должна существовать, но если нет – создадим)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                character_name TEXT DEFAULT 'Мини-Я',
                archetype TEXT,
                game_status TEXT DEFAULT 'not_started',
                test_answers TEXT DEFAULT '[]',
                game_day INTEGER DEFAULT 0,
                game_answers TEXT DEFAULT '[]',
                key_phrases TEXT DEFAULT '[]',
                waiting_for_practice INTEGER DEFAULT 0,
                practice_done INTEGER DEFAULT 0,
                last_day_completed_date TEXT,
                reminder_morning TEXT,
                reminder_day TEXT,
                reminder_evening TEXT,
                custom_tasks TEXT DEFAULT '[]',
                temp_action TEXT,
                temp_data TEXT,
                paused INTEGER DEFAULT 0,
                morning_time TEXT DEFAULT '07:00',
                evening_time TEXT DEFAULT '22:00',
                deep_profile TEXT DEFAULT '{}',
                deep_test_completed INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # practices
        cur.execute('''
            CREATE TABLE IF NOT EXISTS practices (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                name TEXT NOT NULL,
                description TEXT,
                category TEXT,
                schedule_time TEXT,
                schedule_days TEXT,
                is_custom INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # practice_progress
        cur.execute('''
            CREATE TABLE IF NOT EXISTS practice_progress (
                user_id BIGINT,
                practice_id INTEGER,
                completed_count INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                last_used TEXT,
                PRIMARY KEY (user_id, practice_id)
            )
        ''')
        # reports
        cur.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                type TEXT,
                content TEXT,
                reply TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # НОВОЕ: таблица для истории сообщений
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    conn.close()

init_db()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def get_user(user_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    user = dict(row)
    user['test_answers'] = json.loads(user.get('test_answers', '[]') or '[]')
    user['game_answers'] = json.loads(user.get('game_answers', '[]') or '[]')
    user['key_phrases'] = json.loads(user.get('key_phrases', '[]') or '[]')
    user['custom_tasks'] = json.loads(user.get('custom_tasks', '[]') or '[]')
    user['deep_profile'] = json.loads(user.get('deep_profile', '{}') or '{}')
    return user

def get_or_create_user(user_id, username=None):
    user = get_user(user_id)
    if user is None:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                (user_id, username)
            )
            conn.commit()
        conn.close()
        user = get_user(user_id)
    return user

def save_user_field(user_id, field, value):
    conn = get_db_connection()
    with conn.cursor() as cur:
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        cur.execute(f"UPDATE users SET {field} = %s WHERE user_id = %s", (value, user_id))
        conn.commit()
    conn.close()

def delete_user(user_id):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
        conn.commit()
    conn.close()
    logging.info(f"User {user_id} deleted")

# ---------- АРХЕТИПЫ ----------
ARCHETYPE_NAMES = {
    "INTJ": "Дирижёр", "ENTJ": "Командир", "INTP": "Мыслитель", "ENTP": "Новатор",
    "INFJ": "Наставник", "ENFJ": "Вдохновитель", "ISTJ": "Хранитель", "ESTJ": "Администратор",
    "ISFJ": "Заботливый", "ESFJ": "Душа компании", "ISTP": "Мастер", "ESTP": "Искатель",
    "ISFP": "Художник", "ESFP": "Жизнелюб", "INFP": "Идеалист", "ENFP": "Исследователь"
}

ARCHETYPE_DESC = {
    "INTJ": (
        "Ты — Дирижёр. Там, где другие видят хаос, ты видишь будущую систему. "
        "Тебе легко держать в голове большую картину: куда всё движется, что важно, а что — шум. "
        "Люди рядом часто удивляются, как у тебя «всё продумано».\n\n"
        "Решения ты принимаешь головой: взвешиваешь, просчитываешь, смотришь на несколько шагов вперёд. "
        "Это делает тебя надёжным стратегом, но иногда мешает услышать собственные чувства — они кажутся «нелогичными» и откладываются на потом.\n\n"
        "Наполняет тебя порядок и прогресс: когда план работает, когда сложное становится понятным, когда есть время подумать в тишине. "
        "Истощает — суета, бесконечные согласования и люди, которые «просто поговорить».\n\n"
        "💫 На что обратить внимание: ты часто берёшь на себя больше, чем нужно, и тянешь до последнего. "
        "Усталость подкрадывается незаметно — ты замечаешь её, когда уже на нуле.\n\n"
        "Как использовать «Якорь»: вечерний отчёт — твой главный инструмент. "
        "Он поможет замечать не только сделанное, но и то, как ты себя при этом чувствуешь. "
        "А чек-ин дня подскажет связь: в какие дни ты успеваешь позаботиться о себе — и как это меняет твоё состояние."
    ),
    "ENTJ": "Ты — Командир...",  # Добавь полные описания
    "INTP": "Ты — Мыслитель...",
    "ENTP": "Ты — Новатор...",
    "INFJ": "Ты — Наставник...",
    "ENFJ": "Ты — Вдохновитель...",
    "ISTJ": "Ты — Хранитель...",
    "ESTJ": "Ты — Администратор...",
    "ISFJ": "Ты — Заботливый...",
    "ESFJ": "Ты — Душа компании...",
    "ISTP": "Ты — Мастер...",
    "ESTP": "Ты — Искатель...",
    "ISFP": "Ты — Художник...",
    "ESFP": "Ты — Жизнелюб...",
    "INFP": "Ты — Идеалист...",
    "ENFP": "Ты — Исследователь..."
}

# ---------- ДРУГИЕ ДАННЫЕ (блоки, аффирмации, живые ответы) ----------
BLOCKS = {
    "N-1": "Ты заметил(а) это. Уже хорошо. А теперь спроси себя: 'Это моё или чужое?'",
    "N-2": "Ты не обязан(а) тащить всё на себе. Иногда достаточно просто быть.",
    "N-3": "Ты — не ошибка. Ты — точка отсчёта.",
    "N-4": "Твоя сила — в том, что ты умеешь держать ритм. Но ритм — это не бег. Это дыхание.",
    "N-5": "Порядок — это не когда всё на месте. Это когда всё дышит.",
    "N-6": "Ты — не один(а). Твоя тень — тоже часть тебя. Прими её.",
    "N-7": "То, что ты сегодня сделал(а) — достаточно. Ты — достаточно.",
    "N-8": "Ты — тот, кто зажигает свет, даже когда никто не смотрит.",
    "N-9": "Иногда лучшее, что можно сделать — это остановиться и выдохнуть.",
    "N-10": "Ты — не хозяин дня. Ты — участник. И это нормально.",
}

AFFIRMATIONS = {
    "INTJ": "Я строю системы, которые служат жизни.",
    "ENTJ": "Я веду, но не подавляю.",
    "INTP": "Мои мысли — мои инструменты, а не мои тюрьмы.",
    "ENTP": "Я завершаю то, что начинаю.",
    "INFJ": "Я забочусь о себе так же, как о мире.",
    "ENFJ": "Я заряжаюсь от тишины, а не только от людей.",
    "ISTJ": "Гибкость — моя новая сила.",
    "ESTJ": "Я смягчаю свою требовательность.",
    "ISFJ": "Мои желания — тоже важны.",
    "ESFJ": "Я опираюсь на себя, а не на чужие оценки.",
    "ISTP": "Я позволяю себе обязательства.",
    "ESTP": "Я думаю перед действием.",
    "ISFP": "Я не прячусь от критики.",
    "ESFP": "Я не боюсь глубоких тем.",
    "INFP": "Я воплощаю мечты в шаги.",
    "ENFP": "Я довожу дела до конца."
}

LIVELY_RESPONSES = [
    "А я сегодня молодец, что заметил это! Ой, то есть ты молодец! 😄",
    "Ого, ты это сказал(а)! Ну, я бы тоже так сказал, но я же бот. Так что ты — молодец.",
    "Слушай, а я сегодня тоже так подумал! Нет, постой, это же ты подумал. Ладно, ты молодец, а я просто бот.",
    "Ты это серьёзно? Я бы на твоём месте тоже так сказал. Потому что я — это ты. Ну, почти.",
    "О! Я как раз хотел(а) это сказать! Но ты сказал(а) первым(ой). Так что тебе — респект.",
    "Ахаха, я бы не смог(ла) так честно. Потому что я не умею врать. Ты — молодец!",
]

def get_lively_response():
    return random.choice(LIVELY_RESPONSES)

# ---------- ГЛУБОКИЙ ТЕСТ ----------
DEEP_QUESTIONS = [
    {
        "id": "crisis",
        "text": "Когда всё рушится, твой первый импульс?",
        "options": [
            {"label": "Найти выход. Построить мост.", "archetypes": ["ENTP", "ENFP"]},
            {"label": "Взять контроль. Восстановить порядок.", "archetypes": ["INTJ", "ENTJ"]},
            {"label": "Уйти. Наблюдать. Понять, что происходит.", "archetypes": ["INTP", "INFJ"]},
            {"label": "Защитить тех, кто слабее.", "archetypes": ["ISTJ", "ISFJ"]},
            {"label": "Создать комфорт. Сохранить тепло.", "archetypes": ["ESFJ", "ISFP"]},
            {"label": "Показать, что это абсурд. Разрядить.", "archetypes": ["ESTP", "ENFP"]},
            {"label": "Углубиться в чувство. Найти красоту в разрушении.", "archetypes": ["INFP"]},
            {"label": "Найти, чему научиться. Передать другим.", "archetypes": ["ENFJ"]},
        ],
    },
    {
        "id": "home",
        "text": "Что для тебя — «дом»?",
        "options": [
            {"label": "Место, где я свободен идти.", "archetypes": ["ENTP", "ENFP"]},
            {"label": "Место, где всё на своих местах.", "archetypes": ["INTJ", "ISTJ"]},
            {"label": "Место, где меня понимают без слов.", "archetypes": ["INTP", "INFJ"]},
            {"label": "Место, где все в безопасности.", "archetypes": ["ISFJ", "ESTJ"]},
            {"label": "Место, где тепло и можно просто быть.", "archetypes": ["ESFJ", "ISFP"]},
            {"label": "Место, где смеются над важным.", "archetypes": ["ESTP", "ENFP"]},
            {"label": "Место, где растут.", "archetypes": ["ENFJ", "INFP"]},
        ],
    },
    {
        "id": "shadow",
        "text": "Твоя тень — что ты скрываешь даже от себя?",
        "options": [
            {"label": "Я бегу, прежде чем останусь.", "archetypes": ["ENTP", "ENFP"]},
            {"label": "Я ломаю, прежде чем построю.", "archetypes": ["ESTP", "INTJ"]},
            {"label": "Я контролирую, потому что боюсь хаоса.", "archetypes": ["INTJ", "ISTJ"]},
            {"label": "Я знаю всё, но не действую.", "archetypes": ["INTP", "INFJ"]},
            {"label": "Я отдаю, чтобы не чувствовать пустоту.", "archetypes": ["ISFJ", "ENFJ"]},
            {"label": "Я сглаживаю, чтобы не выбирать.", "archetypes": ["ESFJ", "ISFP"]},
            {"label": "Я смеюсь, чтобы не плакать.", "archetypes": ["ESTP", "ENFP"]},
            {"label": "Я спасаю, чтобы не быть обычным.", "archetypes": ["ENFJ", "INFP"]},
        ],
    }
]

def calculate_deep_profile(answers):
    scores = {code: 0 for code in ARCHETYPE_NAMES.keys()}
    for q in DEEP_QUESTIONS:
        qid = q["id"]
        selected = answers.get(qid)
        if not selected:
            continue
        for opt in q["options"]:
            if opt["label"] == selected:
                for arch in opt["archetypes"]:
                    if arch in scores:
                        scores[arch] += 1
                break
    sorted_arch = sorted(scores.items(), key=lambda x: -x[1])
    core = sorted_arch[0][0]
    support = sorted_arch[1][0] if len(sorted_arch) > 1 else core
    shadow = sorted_arch[-1][0] if sorted_arch[-1][0] != core else sorted_arch[-2][0]
    return {
        "core": core,
        "support": support,
        "shadow": shadow,
        "scores": scores,
    }

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
            logging.error(f"Send error attempt {attempt+1}: {e}")
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
        return requests.post(url, json=payload, timeout=10).json()
    except Exception as e:
        logging.error(f"Keyboard send error: {e}")
        return None

def answer_callback(callback_id, text=''):
    url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={'callback_query_id': callback_id, 'text': text}, timeout=5)
    except:
        pass

# ---------- КЛАВИАТУРЫ ----------
def get_main_menu():
    main_menu = [
        ["🏠 Главная", "🧠 Мой Архетип"],
        ["📋 Расписание", "🚪 Комната"],
        ["📊 Прогресс", "🧘 Практики"],
        ["⚙️ Настройки"]
    ]
    # Добавим кнопку "История" в отдельный ряд (или вставить в ряд с чем-то)
    # Чтобы не раздувать меню, добавим её как отдельную кнопку в конце
    main_menu.append(["📖 История"])
    keyboard = {
        'keyboard': [[{'text': btn} for btn in row] for row in main_menu],
        'resize_keyboard': True,
        'one_time_keyboard': False
    }
    return keyboard

def show_main_menu(chat_id, text, waiting=False, paused=False):
    main_menu = [
        ["🏠 Главная", "🧠 Мой Архетип"],
        ["📋 Расписание", "🚪 Комната"],
        ["📊 Прогресс", "🧘 Практики"],
        ["⚙️ Настройки"]
    ]
    if waiting:
        main_menu.insert(0, ["✅ Выполнил(а) практику"])
    if paused:
        main_menu.insert(0, ["▶️ Возобновить"])
    main_menu.append(["📖 История"])  # Всегда добавляем историю
    keyboard = {
        'keyboard': [[{'text': btn} for btn in row] for row in main_menu],
        'resize_keyboard': True,
        'one_time_keyboard': False
    }
    send_keyboard(chat_id, text, keyboard)

def show_submenu(chat_id, text, buttons):
    keyboard = {
        'keyboard': [[{'text': btn} for btn in row] for row in buttons],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    send_keyboard(chat_id, text, keyboard)

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ЛОГИКИ ----------
def escape_markdown(text):
    if not isinstance(text, str):
        return text
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{c}' if c in escape_chars else c for c in text)

def get_archetype_name(code):
    return ARCHETYPE_NAMES.get(code, "Человек")

def get_affirmation(archetype):
    return AFFIRMATIONS.get(archetype, "Ты на правильном пути.")

def get_block_text(block_id):
    return BLOCKS.get(block_id, "Ты замечаешь важное. Продолжай.")

def get_lively_reply(block_id, user_name):
    block = get_block_text(block_id)
    lively = get_lively_response()
    return f"{lively}\n\n{block}"

def extract_key_phrase(text):
    words = text.split()
    if len(words) <= 7:
        return text[:60] + "..." if len(text) > 60 else text
    return ' '.join(words[:7]) + "..."

# ---------- КОМНАТА (7 дней) ----------
GAME_DAYS = {
    1: {
        "title": "Первый шаг",
        "question": "Твой Мини-Ты смотрит на тебя и говорит: 'Я здесь, чтобы слушать тебя. Без оценок, без советов. Просто хочу знать, что внутри.'\n\nРасскажи мне: **как ты себя чувствуешь сегодня?**",
        "response": "Я слышу. Это был первый уровень. Ты просто сказал(а) то, что есть. Иногда это самое сложное."
    },
    2: {
        "title": "То, что внутри",
        "question": "Твой Мини-Ты говорит: 'Есть вещи, которые мы носим внутри, но не говорим вслух. Они становятся тяжелее, когда мы их держим в себе.'\n\nРасскажи мне: **что ты держишь внутри — и что тебе хочется отпустить?**",
        "response": "Ты говоришь о том, что тяжёлое. Это не слабость — это смелость. Второй уровень. Мы становимся ближе."
    },
    3: {
        "title": "То, что наполняет",
        "question": "Твой Мини-Ты говорит: 'Сегодня я хочу спросить о том, что наполняет. Ты знаешь, что есть моменты, когда ты чувствуешь себя живым(ой)?'\n\nРасскажи мне: **что сегодня заставило тебя почувствовать себя живым(ой)?**",
        "response": "Ты говоришь о светлом. Третий уровень. Ты не только видишь сложное, но и замечаешь, что даёт тебе силу."
    },
    4: {
        "title": "То, что ты боишься",
        "question": "Твой Мини-Ты говорит: 'Но есть вещи, которые мы боимся сказать даже самим себе. Потому что страшно их признавать.'\n\nРасскажи мне: **чего ты боишься по-настоящему?**",
        "response": "Ты сказал(а) это вслух. Твой страх потерял часть своей силы. Четвёртый уровень. Ты готов(а) смотреть в лицо тому, что пугает."
    },
    5: {
        "title": "То, что ты хочешь",
        "question": "Твой Мини-Ты говорит: 'Теперь я хочу спросить о том, что ты хочешь. Не о том, что 'надо', а о том, что ты действительно хочешь для себя.'\n\nРасскажи мне: **чего ты хочешь по-настоящему?**",
        "response": "Ты говоришь о желаниях. Пятый уровень. Ты начинаешь отличать 'надо' от 'хочу'."
    },
    6: {
        "title": "То, что ты можешь изменить",
        "question": "Твой Мини-Ты говорит: 'Не всё в наших руках. Но что-то — точно. Ты знаешь, что это может быть?'\n\nРасскажи мне: **что в твоей жизни зависит от тебя — и ты готов(а) это изменить?**",
        "response": "Ты говоришь о том, что в твоих руках. Шестой уровень. Ты видишь не только то, что есть, но и то, что может быть."
    },
    7: {
        "title": "Комната",
        "question": "Твой Мини-Ты говорит: 'Мы прошли 7 дней вместе. Сейчас я открою тебе Комнату, которую мы построили вместе. В ней — ты.'\n\nРасскажи мне: **что ты чувствуешь, глядя на этот путь?**",
        "response": "Это был твой путь. Я просто был(а) рядом. Спасибо, что позволил(а) мне быть твоим Мини-Ты."
    }
}

def get_game_day(day):
    return GAME_DAYS.get(day, GAME_DAYS[1])

def show_game_question(chat_id, user_id, day):
    day_data = get_game_day(day)
    text = f"🚪 *День {day} из 7: {day_data['title']}*\n\n{day_data['question']}"
    buttons = []
    if day > 1:
        buttons.append(["🔙 Назад"])
    buttons.append(["🚪 Выйти из комнаты"])
    keyboard = {
        'keyboard': [[{'text': btn} for btn in row] for row in buttons],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    send_keyboard(chat_id, text, keyboard)

def handle_game_answer(chat_id, user_id, user, text):
    day = user.get('game_day', 0)
    if text.startswith('/') or text in ["🔙 Назад", "🚪 Выйти из комнаты", "🏠 Главная", "🧠 Мой Архетип", "📋 Расписание", "🚪 Комната", "📊 Прогресс", "⚙️ Настройки"]:
        return
    day_data = get_game_day(day)
    phrase = extract_key_phrase(text)
    answers = user.get('game_answers', [])
    answers.append(text)
    save_user_field(user_id, 'game_answers', answers)
    phrases = user.get('key_phrases', [])
    phrases.append(phrase)
    save_user_field(user_id, 'key_phrases', phrases)
    next_day = day + 1
    today_str = date.today().isoformat()
    save_user_field(user_id, 'last_day_completed_date', today_str)
    save_user_field(user_id, 'waiting_for_practice', 1)
    save_user_field(user_id, 'game_status', 'idle')
    if next_day > 7:
        send_message(chat_id, f"✅ *День {day} завершён!*\n\n{day_data['response']}\n\n🎉 Ты прошёл(ла) все 7 дней! Осталось выполнить практику, чтобы завершить путешествие.")
        # Предложение глубокого теста
        deep_text = "🧠 Хочешь узнать свой глубинный архетип? Пройди **Глубокий тест** — 3 вопроса, которые раскроют твоё Ядро, Опора и Тень.\nНажми /deep или выбери в настройках."
        send_message(chat_id, deep_text)
    else:
        send_message(chat_id, f"✅ *День {day} завершён!*\n\n{day_data['response']}\n\n📅 *Завтра — День {next_day}.*\nЧтобы открыть следующий день, выполни практику из расписания и нажми '✅ Выполнил(а) практику'.\nНовый день откроется только завтра.")
    show_main_menu(chat_id, "Главное меню:", waiting=True, paused=user.get('paused', 0))

def build_room(user):
    phrases = user.get('key_phrases', [])
    name = user.get('character_name', 'Мини-Я')
    archetype_code = user.get('archetype', '')
    archetype_name = get_archetype_name(archetype_code)
    if not phrases:
        return f"🏠 *Комната {escape_markdown(name)}*\n\nТы ещё не прошёл(а) ни одного дня. Начни путешествие!"
    text = f"🏠 *Комната {escape_markdown(name)}*\n\n"
    text += f"Ты — {archetype_name}. Ты прошёл(ла) {len(phrases)} из 7 дней.\n\n"
    text += "Твои слова, которые остались со мной:\n\n"
    for i, phrase in enumerate(phrases, 1):
        text += f"{i}. {escape_markdown(phrase)}\n"
    if len(phrases) >= 7:
        text += "\n✨ Ты завершил(а) путешествие! Комната наполнилась твоими голосами."
    return text

def show_room(chat_id, user):
    room_text = build_room(user)
    send_message(chat_id, room_text)

# ---------- РАСПИСАНИЕ ----------
SCHEDULE = {
    "INTJ": {
        "morning": ("Утро Дирижёра", "Запиши 3 приоритета дня — без лишнего.", "Я строю то, что имеет значение."),
        "day": ("День Дирижёра", "Убери одно ненужное дело из списка.", "Мой фокус — моя суперсила."),
        "evening": ("Вечер Дирижёра", "Оцени, что пошло по плану, а что — нет.", "Я контролирую важное и отпускаю лишнее.")
    },
    # Добавь остальные архетипы по аналогии (или можно использовать предыдущий код)
}

def get_schedule(archetype):
    return SCHEDULE.get(archetype, SCHEDULE.get("INTJ"))

# ---------- НОВАЯ ФУНКЦИЯ: ИСТОРИЯ ----------
def handle_history(chat_id, user_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT message, created_at FROM user_messages WHERE user_id = %s ORDER BY created_at DESC LIMIT 50",
            (user_id,)
        )
        rows = cur.fetchall()
    conn.close()

    if not rows:
        send_message(chat_id, "📖 История пуста. Напиши что-нибудь, и я сохраню.")
        show_main_menu(chat_id, "Главное меню:")
        return

    history_text = "📖 *Твоя полная история:*\n\n"
    for row in rows:
        dt = row['created_at'].strftime('%d.%m.%Y %H:%M') if row['created_at'] else ''
        history_text += f"*{dt}*\n{row['message']}\n\n"
        history_text += "—" * 30 + "\n\n"

    if len(history_text) > 4000:
        parts = [history_text[i:i+4000] for i in range(0, len(history_text), 4000)]
        for i, part in enumerate(parts):
            send_message(chat_id, f"📖 *История (часть {i+1}/{len(parts)})*\n\n{part}")
    else:
        send_message(chat_id, history_text)

    show_main_menu(chat_id, "Главное меню:")

# ---------- ОБРАБОТЧИКИ СООБЩЕНИЙ ----------
def handle_start(chat_id, user_id):
    # СНИМАЕМ ПАУЗУ
    user = get_user(user_id)
    if user and user.get('paused', 0):
        save_user_field(user_id, 'paused', 0)
    user = get_or_create_user(user_id)
    name = user.get('character_name', 'Мини-Я')
    welcome_text = (
        f"👋 Привет, {escape_markdown(name)}!\n\n"
        "Я — твой Мини-Ты. Я здесь, чтобы помочь тебе лучше понять себя.\n"
        "Выбирай, с чего начнём 👇"
    )
    show_main_menu(chat_id, welcome_text)

# ---------- ОСНОВНОЙ ХЕНДЛЕР ----------
@app.route('/')
def index():
    return "Бот работает!"

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        logging.info(f"Получен запрос: {data}")

        if 'callback_query' in data:
            cb = data['callback_query']
            chat_id = cb['message']['chat']['id']
            user_id = cb['from']['id']
            username = cb['from'].get('username')
            callback_data = cb['data']
            callback_id = cb['id']

            user = get_or_create_user(user_id, username)

            # Обработка callback'ов (глубокий тест, практики и т.д.)
            if callback_data.startswith('deep_'):
                parts = callback_data.split('_')
                qid = parts[1]
                opt_idx = int(parts[2])
                # Логика обработки глубокого теста (если нужна)
                # Пока заглушка
                answer_callback(callback_id, "Выбор принят")
                return 'ok', 200

            if callback_data.startswith('practice_done:'):
                # Отметка практики выполненной
                practice_id = callback_data.split(':')[1]
                # Обновить прогресс
                answer_callback(callback_id, "Отмечено!")
                return 'ok', 200

            # Другие callback'ы...
            answer_callback(callback_id)
            return 'ok', 200

        if 'message' in data:
            msg = data['message']
            chat_id = msg['chat']['id']
            user_id = msg['from']['id']
            username = msg['from'].get('username')
            text = msg.get('text', '')

            if not text:
                return 'ok', 200

            user = get_or_create_user(user_id, username)

            # Проверка паузы
            if user and user.get('paused', 0) and text not in ['▶️ Возобновить', '/start']:
                send_message(chat_id, "⏸ Бот на паузе. Чтобы возобновить, нажми ▶️ Возобновить.")
                return 'ok', 200

            # Обработка команд
            if text.startswith('/'):
                if text == '/start':
                    handle_start(chat_id, user_id)
                elif text == '/reset_me':
                    delete_user(user_id)
                    send_message(chat_id, "🔄 Аккаунт сброшен. Напиши /start для начала.")
                else:
                    show_main_menu(chat_id, "Главное меню:")
                return 'ok', 200

            # Кнопки меню
            if text == "🏠 Главная":
                show_main_menu(chat_id, f"Главное меню, {escape_markdown(user.get('character_name', 'Мини-Я'))}:")
                return 'ok', 200

            if text == "🧠 Мой Архетип":
                # Показать архетип
                send_message(chat_id, "Функция в разработке")
                return 'ok', 200

            if text == "📋 Расписание":
                archetype = user.get('archetype')
                if archetype:
                    sched = get_schedule(archetype)
                    # Показать расписание
                    send_message(chat_id, "Расписание загружено")
                else:
                    send_message(chat_id, "Сначала пройди тест на архетип.")
                return 'ok', 200

            if text == "🚪 Комната":
                # Логика входа в комнату
                status = user.get('game_status', 'idle')
                day = user.get('game_day', 0)
                waiting = user.get('waiting_for_practice', 0)

                if waiting:
                    send_message(chat_id, "🔒 Ты завершил(а) день, но чтобы перейти к следующему, выполни практику из расписания и нажми '✅ Выполнил(а) практику'.")
                    show_main_menu(chat_id, "Главное меню:", waiting=True, paused=user.get('paused', 0))
                    return 'ok', 200

                if status == 'idle' and day > 0:
                    save_user_field(user_id, 'game_status', 'active')
                    show_game_question(chat_id, user_id, day)
                elif status == 'idle' and day == 0:
                    save_user_field(user_id, 'game_status', 'active')
                    save_user_field(user_id, 'game_day', 1)
                    show_game_question(chat_id, user_id, 1)
                elif status == 'active':
                    show_game_question(chat_id, user_id, day)
                elif status == 'completed':
                    show_room(chat_id, user)
                else:
                    send_message(chat_id, "Сначала пройди тест на архетип.")
                return 'ok', 200

            if text == "📊 Прогресс":
                # Показать прогресс
                send_message(chat_id, "Прогресс: ...")
                return 'ok', 200

            if text == "🧘 Практики":
                # Список практик
                send_message(chat_id, "Список практик...")
                return 'ok', 200

            if text == "⚙️ Настройки":
                # Настройки
                send_message(chat_id, "Настройки...")
                return 'ok', 200

            if text == "✅ Выполнил(а) практику":
                if user.get('waiting_for_practice', 0):
                    save_user_field(user_id, 'waiting_for_practice', 0)
                    save_user_field(user_id, 'practice_done', 1)
                    next_day = user.get('game_day', 0) + 1
                    if next_day <= 7:
                        save_user_field(user_id, 'game_day', next_day)
                        save_user_field(user_id, 'game_status', 'active')
                        send_message(chat_id, f"✅ Отлично! Переходим к Дню {next_day}.")
                        show_game_question(chat_id, user_id, next_day)
                    else:
                        save_user_field(user_id, 'game_status', 'completed')
                        send_message(chat_id, "🎉 Поздравляю! Ты прошёл(ла) все 7 дней!")
                    show_main_menu(chat_id, "Главное меню:", waiting=False)
                else:
                    send_message(chat_id, "Тебе не нужно отмечать практику прямо сейчас.")
                return 'ok', 200

            if text == "📖 История":
                handle_history(chat_id, user_id)
                return 'ok', 200

            if text == "▶️ Возобновить":
                save_user_field(user_id, 'paused', 0)
                show_main_menu(chat_id, "✅ Бот возобновлён.")
                return 'ok', 200

            # Если это ответ на вопрос комнаты (активный статус)
            if user.get('game_status') == 'active':
                handle_game_answer(chat_id, user_id, user, text)
                return 'ok', 200

            # Иначе сохраняем как отчёт и в историю
            # Сохраняем сообщение в историю
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_messages (user_id, message) VALUES (%s, %s)",
                    (user_id, text)
                )
                conn.commit()
            conn.close()

            # Сохраняем в reports (старая логика)
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO reports (user_id, type, content) VALUES (%s, %s, %s)",
                    (user_id, 'general', text)
                )
                conn.commit()
            conn.close()

            send_message(chat_id, "📝 Сохранил твои мысли. Спасибо!")
            show_main_menu(chat_id, "Главное меню:")

        return 'ok', 200
    except Exception as e:
        logging.error(f"Webhook error: {e}", exc_info=True)
        return 'ok', 200

# ---------- ЗАПУСК ----------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
