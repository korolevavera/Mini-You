\# -*- coding: utf-8 -*-
import os
import json
import sqlite3
import logging
import re
import time
from datetime import datetime, timedelta, date
from flask import Flask, request
import requests

app = Flask(__name__)

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set")

# Секретный ключ для cron (можно задать через переменную окружения)
CRON_KEY = os.environ.get('CRON_KEY', 'my_secret_key_123')

logging.basicConfig(level=logging.INFO)

# =============================================
# БАЗА ДАННЫХ
# =============================================
DB_PATH = os.path.join(os.path.dirname(__file__), "anchor.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
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
            temp_action TEXT,          -- 'set_morning', 'set_day', 'set_evening', 'add_task', 'delete_task'
            temp_data TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    user = dict(row)
    user['test_answers'] = json.loads(user['test_answers'] or '[]')
    user['game_answers'] = json.loads(user['game_answers'] or '[]')
    user['key_phrases'] = json.loads(user['key_phrases'] or '[]')
    user['custom_tasks'] = json.loads(user['custom_tasks'] or '[]')
    return user

def get_or_create_user(user_id, username=None):
    init_db()
    user = get_user(user_id)
    if user is None:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        conn.commit()
        conn.close()
        user = get_user(user_id)
    return user

def save_user_field(user_id, field, value):
    conn = sqlite3.connect(DB_PATH)
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    conn.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()

def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    logging.info(f"User {user_id} deleted")

# =============================================
# ТЕСТ
# =============================================
QUICK_TEST = [
    {"question": "Когда у тебя свободный вечер, ты скорее...", "option_a": "Побудешь дома в тишине", "option_b": "Позвонишь другу или выйдешь в люди", "a_dim": "I", "b_dim": "E"},
    {"question": "Принимая важное решение, ты опираешься на...", "option_a": "Конкретные факты и опыт", "option_b": "Интуицию и общую картину", "a_dim": "S", "b_dim": "N"},
    {"question": "В трудной ситуации тебе важнее...", "option_a": "Найти логичное решение", "option_b": "Сохранить гармонию", "a_dim": "T", "b_dim": "F"},
    {"question": "Твой подход к делам и планам...", "option_a": "Планирую заранее", "option_b": "Действую по ситуации", "a_dim": "J", "b_dim": "P"},
    {"question": "После насыщенного дня тебе помогает восстановиться...", "option_a": "Тихий отдых наедине с собой", "option_b": "Разговор с близкими", "a_dim": "I", "b_dim": "E"},
    {"question": "Тебе интереснее...", "option_a": "Работать с конкретными вещами", "option_b": "Исследовать идеи и теории", "a_dim": "S", "b_dim": "N"},
    {"question": "Как ты справляешься с дедлайнами?", "option_a": "Делаю заранее", "option_b": "Работаю в последний момент", "a_dim": "J", "b_dim": "P"},
    {"question": "В компании незнакомых людей ты...", "option_a": "Быстро вливаешься в разговор", "option_b": "Держишься в стороне", "a_dim": "E", "b_dim": "I"},
    {"question": "При описании события ты обычно...", "option_a": "Точно перечисляешь детали", "option_b": "Передаёшь общее впечатление", "a_dim": "S", "b_dim": "N"},
    {"question": "При принятии решения ты...", "option_a": "Анализируешь все за и против", "option_b": "Прислушиваешься к интуиции", "a_dim": "T", "b_dim": "F"},
    {"question": "Ты предпочитаешь...", "option_a": "Иметь чёткий план на день", "option_b": "Импровизировать по ходу", "a_dim": "J", "b_dim": "P"},
    {"question": "После долгого общения ты чувствуешь...", "option_a": "Прилив энергии и желание продолжать", "option_b": "Усталость и потребность в тишине", "a_dim": "E", "b_dim": "I"}
]

ARCHETYPE_NAMES = {
    "INTJ": "Дирижёр", "ENTJ": "Командир", "INTP": "Мыслитель", "ENTP": "Новатор",
    "INFJ": "Наставник", "ENFJ": "Вдохновитель", "ISTJ": "Хранитель", "ESTJ": "Администратор",
    "ISFJ": "Заботливый", "ESFJ": "Душа компании", "ISTP": "Мастер", "ESTP": "Искатель",
    "ISFP": "Художник", "ESFP": "Жизнелюб", "INFP": "Идеалист", "ENFP": "Исследователь"
}

ARCHETYPE_DESC = {
    "INTJ": "Ты — Дирижёр. Ты строишь системы из хаоса. Твоя зона роста — научиться отдыхать и не брать всё на себя.",
    "ENTJ": "Ты — Командир. Ты ведёшь за собой и достигаешь целей. Твоя зона роста — быть добрее к себе и другим.",
    "INTP": "Ты — Мыслитель. Ты находишь нестандартные решения. Твоя зона роста — переходить от анализа к действию.",
    "ENTP": "Ты — Новатор. Ты генерируешь идеи. Твоя зона роста — доводить начатое до конца.",
    "INFJ": "Ты — Наставник. Ты понимаешь и вдохновляешь людей. Твоя зона роста — заботиться о себе так же, как о других.",
    "ENFJ": "Ты — Вдохновитель. Ты заряжаешь других энергией. Твоя зона роста — не выгорать, замечая свои потребности.",
    "ISTJ": "Ты — Хранитель. Ты надёжная опора. Твоя зона роста — учиться гибкости и переменам.",
    "ESTJ": "Ты — Администратор. Ты наводишь порядок. Твоя зона роста — смягчать требовательность к себе и другим.",
    "ISFJ": "Ты — Заботливый. Ты внимателен к деталям и людям. Твоя зона роста — помнить о своих желаниях.",
    "ESFJ": "Ты — Душа компании. Ты объединяешь людей. Твоя зона роста — находить опору в себе, а не в чужих оценках.",
    "ISTP": "Ты — Мастер. Ты разбираешься в механизмах. Твоя зона роста — не избегать обязательств.",
    "ESTP": "Ты — Искатель. Ты быстро действуешь. Твоя зона роста — думать о последствиях.",
    "ISFP": "Ты — Художник. Ты создаёшь красоту. Твоя зона роста — не прятаться от критики.",
    "ESFP": "Ты — Жизнелюб. Ты наполняешь жизнь красками. Твоя зона роста — не избегать глубоких тем.",
    "INFP": "Ты — Идеалист. Ты видишь хорошее в людях. Твоя зона роста — воплощать мечты в реальность.",
    "ENFP": "Ты — Исследователь. Ты открываешь новые возможности. Твоя зона роста — доводить дела до конца."
}

# =============================================
# РАСПИСАНИЕ (статические практики)
# =============================================
SCHEDULE = {
    "INTJ": {
        "morning": ("Утро Дирижёра", "Запиши 3 приоритета дня — без лишнего.", "Я строю то, что имеет значение."),
        "day": ("День Дирижёра", "Убери одно ненужное дело из списка.", "Мой фокус — моя суперсила."),
        "evening": ("Вечер Дирижёра", "Оцени, что пошло по плану, а что — нет.", "Я контролирую важное и отпускаю лишнее.")
    },
    "ENTJ": {
        "morning": ("Утро Командира", "Определи главный результат дня.", "Я веду — значит, я отвечаю."),
        "day": ("День Командира", "Делегируй одну задачу.", "Сила в доверии команде."),
        "evening": ("Вечер Командира", "Похвали себя за одно решение.", "Каждый день я становлюсь лучшей версией лидера.")
    },
    "INTP": {
        "morning": ("Утро Мыслителя", "Запиши одну идею, которая крутится в голове.", "Мои мысли — мой компас."),
        "day": ("День Мыслителя", "Останови анализ на одну задачу и просто сделай её.", "Действие — лучшая гипотеза."),
        "evening": ("Вечер Мыслителя", "Что сегодня оказалось интереснее, чем ты ожидал?", "Я нахожу смысл в каждом опыте.")
    },
    "ENTP": {
        "morning": ("Утро Новатора", "Выбери одну идею и сделай первый шаг.", "Я превращаю идеи в реальность."),
        "day": ("День Новатора", "Доведи до конца хотя бы одно начатое дело.", "Завершение — тоже творчество."),
        "evening": ("Вечер Новатора", "Что сегодня тебя по-настоящему зажгло?", "Мой огонь не гаснет.")
    },
    "INFJ": {
        "morning": ("Утро Наставника", "Подумай: кому сегодня ты можешь помочь?", "Моя чуткость — дар, а не слабость."),
        "day": ("День Наставника", "Сделай что-то для себя — не для других.", "Я забочусь о себе так же, как о близких."),
        "evening": ("Вечер Наставника", "Назови одну границу, которую ты сегодня удержал.", "Я в безопасности, когда остаюсь собой.")
    },
    "ENFJ": {
        "morning": ("Утро Вдохновителя", "Напиши кому-то тёплое сообщение с утра.", "Моя энергия меняет мир вокруг."),
        "day": ("День Вдохновителя", "Найди минуту тишины только для себя.", "Я заряжаю других, когда сам заряжен."),
        "evening": ("Вечер Вдохновителя", "Что сегодня дало тебе силу, а не забрало её?", "Я умею восстанавливаться.")
    },
    "ISTJ": {
        "morning": ("Утро Хранителя", "Проверь план и расставь задачи по порядку.", "Надёжность начинается с меня."),
        "day": ("День Хранителя", "Позволь себе отступить от одного правила.", "Гибкость — это тоже сила."),
        "evening": ("Вечер Хранителя", "Что сегодня прошло стабильно и надёжно?", "Мой вклад важен.")
    },
    "ESTJ": {
        "morning": ("Утро Администратора", "Составь чёткий список на день.", "Порядок — основа моего успеха."),
        "day": ("День Администратора", "Спроси мнение кого-то, прежде чем принять решение.", "Разные взгляды делают решение сильнее."),
        "evening": ("Вечер Администратора", "Что сегодня удалось организовать лучше всего?", "Я строю — и это ценно.")
    },
    "ISFJ": {
        "morning": ("Утро Заботливого", "Запланируй одну маленькую приятность для себя.", "Моя забота начинается с меня."),
        "day": ("День Заботливого", "Скажи «нет» хотя бы одной просьбе.", "Мои границы — это уважение к себе."),
        "evening": ("Вечер Заботливого", "Кого ты сегодня поддержал? Как ты себя чувствуешь?", "Я достаточно сделал сегодня.")
    },
    "ESFJ": {
        "morning": ("Утро Души компании", "Напланируй одну встречу или созвон с близким.", "Мои связи — моя сила."),
        "day": ("День Души компании", "Сделай что-то без чужого одобрения.", "Я ценен сам по себе."),
        "evening": ("Вечер Души компании", "Кто сегодня подарил тебе тепло?", "Я окружён заботой.")
    },
    "ISTP": {
        "morning": ("Утро Мастера", "Выбери одну задачу и разберись с ней полностью.", "Я решаю проблемы — это моё призвание."),
        "day": ("День Мастера", "Попробуй объяснить кому-то, как ты решил задачу.", "Мои знания ценны для других."),
        "evening": ("Вечер Мастера", "Что сегодня сработало лучше, чем ожидалось?", "Я доверяю своим рукам и голове.")
    },
    "ESTP": {
        "morning": ("Утро Искателя", "Брось вызов себе: сделай что-то необычное утром.", "Я живу в движении."),
        "day": ("День Искателя", "Остановись на 5 минут и подумай о последствиях.", "Осознанность делает меня сильнее."),
        "evening": ("Вечер Искателя", "Что сегодня было по-настоящему живым?", "Каждый день — приключение.")
    },
    "ISFP": {
        "morning": ("Утро Художника", "Найди красоту в одной мелочи вокруг.", "Красота — в простом."),
        "day": ("День Художника", "Создай что-то руками или словами — пусть маленькое.", "Творчество — мой язык."),
        "evening": ("Вечер Художника", "Что сегодня тронуло тебя больше всего?", "Я чувствую — и это моя сила.")
    },
    "ESFP": {
        "morning": ("Утро Жизнелюба", "Запланируй что-то радостное на сегодня.", "Жизнь — это праздник, который я создаю."),
        "day": ("День Жизнелюба", "Посиди 10 минут в тишине, без телефона.", "В тишине я слышу себя."),
        "evening": ("Вечер Жизнелюба", "Что сегодня заставило тебя улыбнуться?", "Я несу свет туда, куда прихожу.")
    },
    "INFP": {
        "morning": ("Утро Идеалиста", "Запиши одну ценность, которая важна тебе сегодня.", "Мои идеалы — моя сила."),
        "day": ("День Идеалиста", "Сделай один конкретный шаг к мечте.", "Мечта живёт в действии."),
        "evening": ("Вечер Идеалиста", "Что сегодня совпало с твоими ценностями?", "Я верен себе.")
    },
    "ENFP": {
        "morning": ("Утро Исследователя", "Что нового ты хочешь попробовать сегодня?", "Мир полон возможностей для меня."),
        "day": ("День Исследователя", "Доведи одно дело до конца — полностью.", "Я умею завершать то, что начинаю."),
        "evening": ("Вечер Исследователя", "Что тебя сегодня вдохновило по-новому?", "Каждый день открывает что-то новое.")
    }
}

# =============================================
# КОМНАТА (7 дней)
# =============================================
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

# =============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================
def escape_markdown(text):
    if not isinstance(text, str):
        return text
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{c}' if c in escape_chars else c for c in text)

def calculate_archetype(answers):
    dims = {'E': 0, 'I': 0, 'S': 0, 'N': 0, 'T': 0, 'F': 0, 'J': 0, 'P': 0}
    for i, ans in enumerate(answers):
        q = QUICK_TEST[i]
        if ans == 0:
            dims[q['a_dim']] += 1
        else:
            dims[q['b_dim']] += 1
    e = 'E' if dims['E'] >= dims['I'] else 'I'
    s = 'S' if dims['S'] >= dims['N'] else 'N'
    t = 'T' if dims['T'] >= dims['F'] else 'F'
    j = 'J' if dims['J'] >= dims['P'] else 'P'
    return e + s + t + j

def extract_key_phrase(text):
    words = text.split()
    if len(words) <= 7:
        return text[:60] + "..." if len(text) > 60 else text
    return ' '.join(words[:7]) + "..."

def get_schedule(archetype):
    return SCHEDULE.get(archetype, SCHEDULE.get("INTJ"))

def get_game_day(day):
    return GAME_DAYS.get(day, GAME_DAYS[1])

def build_room(user):
    phrases = user.get('key_phrases', [])
    name = user.get('character_name', 'Мини-Я')
    archetype_code = user.get('archetype', '')
    archetype_name = ARCHETYPE_NAMES.get(archetype_code, 'Человек')
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

def get_practice_text(archetype, period):
    sched = get_schedule(archetype)
    return sched.get(period, ("", "", ""))

# =============================================
# ОТПРАВКА СООБЩЕНИЙ
# =============================================
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
            logging.info(f"Сообщение отправлено (попытка {attempt+1}), статус {r.status_code}")
            return r.json()
        except Exception as e:
            logging.error(f"Ошибка отправки (попытка {attempt+1}): {e}")
            time.sleep(1)
    logging.error(f"Не удалось отправить сообщение после {retries} попыток")
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
        logging.error(f"Ошибка отправки клавиатуры: {e}")
        return None

def show_main_menu(chat_id, text):
    main_menu = [
        ["🏠 Главная", "🧠 Мой Архетип"],
        ["📋 Расписание", "🚪 Комната"],
        ["📊 Прогресс", "⚙️ Настройки"]
    ]
    keyboard = {
        'keyboard': [[{'text': btn} for btn in row] for row in main_menu],
        'resize_keyboard': True,
        'one_time_keyboard': False
    }
    send_keyboard(chat_id, text, keyboard)

def show_test_question(chat_id, user_id, index):
    q = QUICK_TEST[index]
    text = f"🧠 *Вопрос {index+1} из 12*\n\n{q['question']}"
    buttons = [
        [f"А: {q['option_a']}"],
        [f"Б: {q['option_b']}"]
    ]
    control_buttons = []
    if index > 0:
        control_buttons.append("🔙 Назад")
    control_buttons.append("🚪 Выйти из теста")
    if control_buttons:
        buttons.append(control_buttons)
    keyboard = {
        'keyboard': [[{'text': btn} for btn in row] for row in buttons],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    send_keyboard(chat_id, text, keyboard)

def show_game_question(chat_id, user_id, day):
    day_data = get_game_day(day)
    text = f"🚪 *День {day} из 7: {day_data['title']}*\n\n{day_data['question']}"
    buttons = [
        ["📝 Ответить"]
    ]
    if day > 1:
        buttons.append(["🔙 Назад"])
    buttons.append(["🚪 Выйти из комнаты"])
    keyboard = {
        'keyboard': [[{'text': btn} for btn in row] for row in buttons],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    send_keyboard(chat_id, text, keyboard)

def show_submenu(chat_id, text):
    keyboard = {
        'keyboard': [[{'text': '🔙 Назад'}]],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    send_keyboard(chat_id, text, keyboard)

# =============================================
# НАСТРОЙКИ РАСПИСАНИЯ
# =============================================
def show_settings(chat_id, user):
    name = user.get('character_name', 'Мини-Я')
    morning = user.get('reminder_morning') or 'не установлено'
    day = user.get('reminder_day') or 'не установлено'
    evening = user.get('reminder_evening') or 'не установлено'
    tasks = user.get('custom_tasks', [])
    tasks_text = ""
    if tasks:
        for i, task in enumerate(tasks, 1):
            tasks_text += f"{i}. {task['text']} в {task['time']}\n"
    else:
        tasks_text = "нет"
    
    text = (
        f"⚙️ *Настройки расписания для {escape_markdown(name)}*\n\n"
        f"🌅 Утро: {morning}\n"
        f"☀️ День: {day}\n"
        f"🌙 Вечер: {evening}\n\n"
        f"📝 *Ваши задачи:*\n{tasks_text}\n\n"
        "Выберите действие:"
    )
    buttons = [
        ["🕒 Установить утро", "🕒 Установить день"],
        ["🕒 Установить вечер", "➕ Добавить задачу"],
        ["🗑 Удалить задачу", "🔙 Назад"]
    ]
    keyboard = {
        'keyboard': [[{'text': btn} for btn in row] for row in buttons],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    send_keyboard(chat_id, text, keyboard)

def show_task_list(chat_id, user):
    tasks = user.get('custom_tasks', [])
    if not tasks:
        send_message(chat_id, "У вас нет добавленных задач.")
        show_settings(chat_id, user)
        return
    text = "📝 *Ваши задачи:*\n\n"
    for i, task in enumerate(tasks, 1):
        text += f"{i}. {escape_markdown(task['text'])} в {task['time']}\n"
    text += "\nНапишите номер задачи, чтобы удалить её."
    send_message(chat_id, text)

def delete_task(chat_id, user_id, user, task_index):
    tasks = user.get('custom_tasks', [])
    if task_index < 1 or task_index > len(tasks):
        send_message(chat_id, "❌ Некорректный номер задачи.")
        show_settings(chat_id, user)
        return
    removed = tasks.pop(task_index - 1)
    save_user_field(user_id, 'custom_tasks', tasks)
    send_message(chat_id, f"✅ Задача '{removed['text']}' удалена.")
    show_settings(chat_id, user)

def add_task(chat_id, user_id, user, text):
    # Ожидаем ввод текста задачи и времени
    # Формат: "Задача в 14:00" или просто текст, потом бот спросит время
    # Упростим: бот спросит текст, потом время.
    # Для простоты сейчас сделаем через два шага
    # В этом коде оставлю заглушку — полная реализация требует состояния FSM.
    # Пока предлагаю пользователю ввести в формате "Задача в 14:00"
    # Используем обработку, которая ищет время в тексте.
    import re
    match = re.search(r'(\d{1,2}:\d{2})', text)
    if not match:
        send_message(chat_id, "❌ Не найден формат времени (например, 14:00). Попробуйте ещё раз.")
        return
    time_str = match.group(1)
    task_text = text.replace(match.group(0), '').strip()
    if not task_text:
        task_text = "Напоминание"
    tasks = user.get('custom_tasks', [])
    tasks.append({"text": task_text, "time": time_str})
    save_user_field(user_id, 'custom_tasks', tasks)
    send_message(chat_id, f"✅ Задача '{task_text}' в {time_str} добавлена.")
    show_settings(chat_id, user)

# =============================================
# ОСНОВНАЯ ЛОГИКА
# =============================================
@app.route('/')
def index():
    return "Бот 'Мини-Ты' работает! 🤖"

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        return "Webhook работает!"
    
    try:
        data = request.get_json()
        logging.info(f"Получен запрос: {data}")
        
        if data and 'callback_query' in data:
            callback = data['callback_query']
            chat_id = callback['message']['chat']['id']
            user_id = callback['from']['id']
            username = callback['from'].get('username')
            data_callback = callback['data']
            
            user = get_or_create_user(user_id, username)
            
            if user.get('game_status') != 'not_started':
                send_message(chat_id, "Ты уже зарегистрирован(а)! Используй кнопки меню для навигации.")
                show_main_menu(chat_id, "Главное меню:")
                return 'ok', 200
            
            if data_callback == 'start_game':
                send_message(chat_id, 
                    "Отлично! Давай познакомимся. Как назовём твоего Мини-Ты? Просто напиши имя в ответ."
                )
            return 'ok', 200
        
        if data and 'message' in data:
            msg = data['message']
            chat_id = msg['chat']['id']
            user_id = msg['from']['id']
            username = msg['from'].get('username')
            text = msg.get('text', '')
            voice = msg.get('voice')
            
            if voice:
                send_message(chat_id, "Я пока не умею читать голосовые сообщения. Напиши текст, пожалуйста! 📝")
                return 'ok', 200
            
            if not text or text.strip() == '':
                send_message(chat_id, "Я не понимаю пустые сообщения. Напиши текст или выбери кнопку.")
                return 'ok', 200
            
            if re.match(r'^[\U0001F000-\U0001FFFF]+$', text.strip()):
                send_message(chat_id, "Я не могу обработать только эмодзи. Напиши текст или выбери кнопку.")
                return 'ok', 200
            
            user = get_or_create_user(user_id, username)
            handle_message(chat_id, user_id, user, text)
        
        return 'ok', 200
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        return 'ok', 200

def handle_message(chat_id, user_id, user, text):
    status = user.get('game_status')
    name = user.get('character_name', 'Мини-Я')
    day = user.get('game_day', 0)
    waiting = user.get('waiting_for_practice', 0)
    temp_action = user.get('temp_action')
    temp_data = user.get('temp_data')
    
    # === ФИКС: если пользователь в статусе not_started, но имя уже есть ===
    if status == 'not_started' and user.get('character_name') != 'Мини-Я':
        save_user_field(user_id, 'game_status', 'idle')
        status = 'idle'
        user['game_status'] = 'idle'
    
    # === СЕКРЕТНАЯ КОМАНДА СБРОСА ===
    if text == '/reset_me':
        delete_user(user_id)
        send_message(chat_id, "🔄 Аккаунт полностью сброшен! Теперь ты как новый пользователь.\n\nНапиши /start, чтобы начать с приветствия.")
        return

    # === НОВЫЙ ПОЛЬЗОВАТЕЛЬ ===
    if status == 'not_started':
        if text == '/start':
            welcome_text = (
                "👋 Привет! Я — твой Мини-Ты.\n"
                "Я здесь, чтобы помочь тебе лучше понять себя.\n"
                "Вместе мы пройдём путь, который поможет тебе увидеть свои сильные стороны, найти опору и услышать то, что внутри.\n\n"
                "Вот что мы с тобой сделаем:\n\n"
                "🧠 **Узнаем твой архетип** — твою суперсилу и зону роста.\n"
                "📋 **Составим расписание** с практиками и аффирмациями на каждый день.\n"
                "🚪 **Откроем Комнату** — 7 дней вопросов к себе, которые помогут тебе стать ближе к себе.\n"
                "📊 **Будем смотреть прогресс** — чтобы тебе было видно, сколько уже пройдено.\n\n"
                "Готов(а) заглянуть внутрь себя?\n"
                "Нажми кнопку 👇"
            )
            keyboard = {
                'inline_keyboard': [
                    [{'text': '🚀 Начать', 'callback_data': 'start_game'}]
                ]
            }
            send_keyboard(chat_id, welcome_text, keyboard)
            return
        
        name = text.strip()
        if len(name) >= 2 and len(name) <= 20 and not text.startswith('/') and re.match(r'^[a-zA-Zа-яА-ЯёЁ0-9\s\-]+$', name):
            if re.match(r'^[\s\-]+$', name):
                send_message(chat_id, 
                    "Имя не может состоять только из пробелов или дефисов. Пожалуйста, напиши имя ещё раз."
                )
                return
            
            save_user_field(user_id, 'character_name', name)
            save_user_field(user_id, 'game_status', 'idle')
            
            safe_name = escape_markdown(name)
            welcome_text = (
                f"✅ Имя **{safe_name}** сохранено!\n\n"
                f"Отлично! Теперь у тебя есть спутник — **{safe_name}**.\n\n"
                "Я — твой помощник по самонаблюдению. Вот что я умею:\n\n"
                "🧠 **Мой Архетип** — узнать свою суперсилу и зону роста.\n"
                "📋 **Расписание** — практики и аффирмации на день.\n"
                "🚪 **Комната** — 7 дней вопросов к себе.\n"
                "📊 **Прогресс** — смотреть, сколько пройдено.\n\n"
                "Выбирай, с чего начнём 👇\n\n"
                "P.S. Я советую тебе сразу пройти тест на архетип, чтобы составить твою карту."
            )
            show_main_menu(chat_id, welcome_text)
            return
        
        if len(name) < 2 and len(name) > 0 and not text.startswith('/') and text != '/start':
            send_message(chat_id, 
                "Имя должно состоять минимум из 2 символов. Пожалуйста, напиши имя ещё раз."
            )
            return
        if len(name) > 20 and not text.startswith('/') and text != '/start':
            send_message(chat_id, 
                "Имя слишком длинное (максимум 20 символов). Пожалуйста, напиши более короткое имя."
            )
            return
        
        send_message(chat_id, 
            "Чтобы начать, нажми кнопку '🚀 Начать' или напиши имя своего Мини-Ты."
        )
        return

    # === ОБРАБОТКА СОСТОЯНИЙ (FSM) ===
    if temp_action:
        if temp_action.startswith('set_'):
            # Установка времени для напоминания
            if text.lower() == 'отключить':
                # Отключаем напоминание
                field = {'set_morning': 'reminder_morning', 'set_day': 'reminder_day', 'set_evening': 'reminder_evening'}[temp_action]
                save_user_field(user_id, field, None)
                save_user_field(user_id, 'temp_action', None)
                send_message(chat_id, f"🔕 Напоминание отключено.")
                show_settings(chat_id, user)
                return
            # Проверяем формат времени
            if re.match(r'^\d{1,2}:\d{2}$', text.strip()):
                field = {'set_morning': 'reminder_morning', 'set_day': 'reminder_day', 'set_evening': 'reminder_evening'}[temp_action]
                save_user_field(user_id, field, text.strip())
                save_user_field(user_id, 'temp_action', None)
                period_name = {'set_morning': 'утро', 'set_day': 'день', 'set_evening': 'вечер'}[temp_action]
                send_message(chat_id, f"✅ Время для {period_name} установлено на {text.strip()}")
                show_settings(chat_id, user)
                return
            else:
                send_message(chat_id, "❌ Неверный формат времени. Напишите время в формате ЧЧ:ММ (например, 08:00) или 'отключить'.")
                return
        elif temp_action == 'delete_task':
            # Удаление задачи
            if text.isdigit():
                idx = int(text)
                tasks = user.get('custom_tasks', [])
                if 1 <= idx <= len(tasks):
                    removed = tasks.pop(idx-1)
                    save_user_field(user_id, 'custom_tasks', tasks)
                    save_user_field(user_id, 'temp_action', None)
                    send_message(chat_id, f"✅ Задача '{removed['text']}' удалена.")
                    show_settings(chat_id, user)
                    return
                else:
                    send_message(chat_id, "❌ Некорректный номер задачи. Попробуйте ещё раз.")
                    return
            else:
                send_message(chat_id, "❌ Введите номер задачи (цифру).")
                return
        elif temp_action == 'add_task':
            # Добавление задачи — ожидаем текст, потом время, но мы можем объединить: если в тексте есть время, сразу добавляем
            # Если нет времени, просим ввести время.
            if not temp_data:
                # пользователь ввёл текст задачи (без времени)
                # сохраняем текст во временные данные и просим время
                save_user_field(user_id, 'temp_data', text)
                send_message(chat_id, "✅ Текст задачи сохранён. Теперь введите время в формате ЧЧ:ММ (например, 14:00).")
                return
            else:
                # ожидаем ввод времени
                if re.match(r'^\d{1,2}:\d{2}$', text.strip()):
                    task_text = temp_data
                    tasks = user.get('custom_tasks', [])
                    tasks.append({"text": task_text, "time": text.strip()})
                    save_user_field(user_id, 'custom_tasks', tasks)
                    save_user_field(user_id, 'temp_action', None)
                    save_user_field(user_id, 'temp_data', None)
                    send_message(chat_id, f"✅ Задача '{task_text}' в {text.strip()} добавлена.")
                    show_settings(chat_id, user)
                    return
                else:
                    send_message(chat_id, "❌ Неверный формат времени. Напишите время в формате ЧЧ:ММ (например, 14:00).")
                    return

    # === ЗАРЕГИСТРИРОВАННЫЙ ПОЛЬЗОВАТЕЛЬ ===
    if text.startswith('/'):
        if text == '/start':
            if status == 'testing':
                send_message(chat_id, "Ты проходишь тест! Продолжай отвечать на вопросы.")
                return
            show_main_menu(chat_id, f"Главное меню, {escape_markdown(name)}:")
        elif text == '/test':
            if status == 'testing':
                send_message(chat_id, "Ты уже проходишь тест! Просто отвечай на вопросы.")
                return
            start_test(chat_id, user_id)
        elif text == '/archetype':
            show_archetype(chat_id, user_id)
        elif text == '/room':
            show_room(chat_id, user)
        elif text == '/menu':
            show_main_menu(chat_id, f"Главное меню, {escape_markdown(name)}:")
        else:
            show_main_menu(chat_id, "Неизвестная команда. Используй кнопки меню:")
        return

    # === КНОПКИ МЕНЮ ===
    if text == "🔙 Назад":
        show_main_menu(chat_id, f"Главное меню, {escape_markdown(name)}:")
        return

    if text == "🏠 Главная":
        show_main_menu(chat_id, f"Главное меню, {escape_markdown(name)}:")
        
    elif text == "🧠 Мой Архетип":
        if user.get('archetype'):
            show_archetype(chat_id, user_id)
        else:
            send_message(chat_id, 
                "🧠 Ты ещё не проходил(а) тест.\n\n"
                "Нажми /test, чтобы узнать свой архетип."
            )
            
    elif text == "📋 Расписание":
        archetype = user.get('archetype')
        if archetype:
            sched = get_schedule(archetype)
            name_archetype = ARCHETYPE_NAMES.get(archetype, archetype)
            static_text = f"📋 *Расписание для архетипа «{name_archetype}»*\n\n"
            for period, label in [("morning", "🌅 Утро"), ("day", "☀️ День"), ("evening", "🌙 Вечер")]:
                title, practice, affirmation = sched[period]
                static_text += f"*{label}*\n📌 {practice}\n💬 _{affirmation}_\n\n"
            buttons = [["🔙 Назад"]]
            if waiting:
                buttons.append(["✅ Выполнил(а) практику"])
            keyboard = {
                'keyboard': [[{'text': btn} for btn in row] for row in buttons],
                'resize_keyboard': True,
                'one_time_keyboard': True
            }
            send_keyboard(chat_id, static_text, keyboard)
        else:
            send_message(chat_id, "Сначала определи свой архетип через 🧠 Мой Архетип")
            
    elif text == "🚪 Комната":
        if user.get('character_name') == 'Мини-Я':
            send_message(chat_id, "Сначала зарегистрируйся через /start и дай имя своему Мини-Ты.")
            show_main_menu(chat_id, "Главное меню:")
            return
        
        # Проверка таймера
        if status == 'idle' and day > 0:
            last_date_str = user.get('last_day_completed_date')
            if last_date_str:
                try:
                    last_date = datetime.strptime(last_date_str, '%Y-%m-%d').date()
                    today = date.today()
                    if last_date >= today:
                        send_message(chat_id, 
                            "⏳ Следующий день откроется завтра.\n"
                            "Ты можешь пока посмотреть 📋 Расписание или 📊 Прогресс."
                        )
                        show_main_menu(chat_id, "Главное меню:")
                        return
                except:
                    pass
        
        if waiting:
            send_message(chat_id, 
                "🔒 Ты завершил(а) день, но чтобы перейти к следующему, выполни практику из расписания и нажми '✅ Выполнил(а) практику'."
            )
            show_main_menu(chat_id, "Главное меню:")
            return
        elif status == 'idle' and day > 0:
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
            if user.get('character_name') != 'Мини-Я':
                save_user_field(user_id, 'game_status', 'idle')
                send_message(chat_id, "Восстанавливаем твой прогресс. Нажми 🚪 Комната снова.")
                show_main_menu(chat_id, "Главное меню:")
            else:
                send_message(chat_id, "Сначала пройди тест 🧠 Мой Архетип")
        
    elif text == "📊 Прогресс":
        archetype = user.get('archetype')
        archetype_name = ARCHETYPE_NAMES.get(archetype, 'не определён') if archetype else 'не определён'
        phrases = user.get('key_phrases', [])
        day = user.get('game_day', 0)
        text = f"📊 *Твой прогресс*\n\n"
        text += f"🧠 Архетип: {archetype_name}\n"
        text += f"🚪 Комната: {len(phrases)} из 7 дней\n"
        if phrases:
            text += f"📝 Собрано фраз: {len(phrases)}\n"
            last_phrase = escape_markdown(phrases[-1])
            text += f"🔄 Последняя фраза: {last_phrase[:40]}..."
        else:
            text += f"🔄 Текущий день: {day} (если начат)"
        show_submenu(chat_id, text)
        
    elif text == "⚙️ Настройки":
        show_settings(chat_id, user)
    
    # === КНОПКИ НАСТРОЕК (устанавливают состояние) ===
    elif text.startswith("🕒 Установить утро"):
        save_user_field(user_id, 'temp_action', 'set_morning')
        send_message(chat_id, "Введите время для утренней практики в формате ЧЧ:ММ (например, 08:00) или напишите 'отключить'.")
        return
    elif text.startswith("🕒 Установить день"):
        save_user_field(user_id, 'temp_action', 'set_day')
        send_message(chat_id, "Введите время для дневной практики в формате ЧЧ:ММ (например, 13:00) или напишите 'отключить'.")
        return
    elif text.startswith("🕒 Установить вечер"):
        save_user_field(user_id, 'temp_action', 'set_evening')
        send_message(chat_id, "Введите время для вечерней практики в формате ЧЧ:ММ (например, 21:00) или напишите 'отключить'.")
        return
    elif text.startswith("➕ Добавить задачу"):
        save_user_field(user_id, 'temp_action', 'add_task')
        save_user_field(user_id, 'temp_data', None)  # очищаем предыдущие данные
        send_message(chat_id, "Введите текст задачи (без времени). Затем я попрошу указать время.")
        return
    elif text.startswith("🗑 Удалить задачу"):
        tasks = user.get('custom_tasks', [])
        if not tasks:
            send_message(chat_id, "У вас нет добавленных задач.")
            show_settings(chat_id, user)
            return
        # Показываем список и устанавливаем состояние
        task_list = "\n".join([f"{i+1}. {task['text']} в {task['time']}" for i, task in enumerate(tasks)])
        save_user_field(user_id, 'temp_action', 'delete_task')
        send_message(chat_id, f"📝 *Ваши задачи:*\n\n{task_list}\n\nНапишите номер задачи, которую нужно удалить.")
        return

    # === ОБРАБОТКА ИГРЫ ===
    elif status == 'active':
        handle_game_answer(chat_id, user_id, user, text)
    
    elif status == 'testing':
        handle_test_answer(chat_id, user_id, user, text)
    
    # === НЕИЗВЕСТНЫЙ ТЕКСТ (возможно, попытка добавить задачу без кнопки) ===
    else:
        # Проверяем, не является ли текст форматом "задача в 14:00" для быстрого добавления
        match = re.search(r'(\d{1,2}:\d{2})', text)
        if match:
            time_str = match.group(1)
            task_text = text.replace(match.group(0), '').strip()
            if not task_text:
                task_text = "Напоминание"
            tasks = user.get('custom_tasks', [])
            tasks.append({"text": task_text, "time": time_str})
            save_user_field(user_id, 'custom_tasks', tasks)
            send_message(chat_id, f"✅ Задача '{task_text}' в {time_str} добавлена.")
            show_settings(chat_id, user)
            return
        else:
            # Если пользователь написал что-то другое
            send_message(chat_id, "Используй кнопки меню 👇")
            show_main_menu(chat_id, "Главное меню:")

# =============================================
# ЛОГИКА ТЕСТА
# =============================================
def start_test(chat_id, user_id):
    save_user_field(user_id, 'test_answers', '[]')
    save_user_field(user_id, 'game_status', 'testing')
    show_test_question(chat_id, user_id, 0)

def handle_test_answer(chat_id, user_id, user, text):
    answers = user['test_answers']
    current_index = len(answers)
    
    if text == "🚪 Выйти из теста":
        save_user_field(user_id, 'game_status', 'idle')
        send_message(chat_id, "Тест прерван. Ты можешь начать его снова через /test.")
        show_main_menu(chat_id, "Главное меню:")
        return
    
    if text == "🔙 Назад":
        if current_index > 0:
            answers.pop()
            save_user_field(user_id, 'test_answers', answers)
            show_test_question(chat_id, user_id, current_index - 1)
        else:
            save_user_field(user_id, 'game_status', 'idle')
            send_message(chat_id, "Ты вернулся(ась) в главное меню.")
            show_main_menu(chat_id, "Главное меню:")
        return
    
    if current_index >= 12:
        finish_test(chat_id, user_id)
        return
    
    if text.startswith("А:") or text.startswith("А") or text.startswith("Б:") or text.startswith("Б"):
        if text.startswith("А"):
            answers.append(0)
        else:
            answers.append(1)
        save_user_field(user_id, 'test_answers', answers)
        
        next_index = len(answers)
        if next_index >= 12:
            finish_test(chat_id, user_id)
        else:
            show_test_question(chat_id, user_id, next_index)
    else:
        send_message(chat_id, "Пожалуйста, выбери вариант А или Б, нажав на кнопку.")

def finish_test(chat_id, user_id):
    user = get_user(user_id)
    answers = user['test_answers']
    
    if len(answers) < 12:
        send_message(chat_id, "Что-то пошло не так. Попробуй ещё раз через /test")
        return
    
    archetype = calculate_archetype(answers)
    save_user_field(user_id, 'archetype', archetype)
    save_user_field(user_id, 'game_status', 'idle')
    
    archetype_name = ARCHETYPE_NAMES.get(archetype, "Человек")
    description = ARCHETYPE_DESC.get(archetype, "Твой уникальный архетип.")
    
    send_message(chat_id,
        f"🧠 *Твой архетип — {archetype_name}*\n\n"
        f"{description}\n\n"
        "Теперь ты можешь:\n"
        "📋 Посмотреть расписание для своего архетипа\n"
        "🚪 Войти в Комнату и начать прокачку\n"
        "📊 Отслеживать прогресс\n\n"
        "Хочешь узнать о своём архетипе больше? Нажми /archetype."
    )
    show_main_menu(chat_id, "Главное меню:")

def show_archetype(chat_id, user_id):
    user = get_user(user_id)
    archetype = user.get('archetype')
    if not archetype:
        send_message(chat_id, "Ты ещё не проходил(а) тест. Напиши /test, чтобы начать.")
        return
    
    archetype_name = ARCHETYPE_NAMES.get(archetype, "Человек")
    description = ARCHETYPE_DESC.get(archetype, "Твой уникальный архетип.")
    
    send_message(chat_id,
        f"🧠 *Твой архетип — {archetype_name}*\n\n"
        f"{description}"
    )

# =============================================
# ЛОГИКА КОМНАТЫ
# =============================================
def handle_game_answer(chat_id, user_id, user, text):
    day = user.get('game_day', 0)
    
    if text == "🚪 Выйти из комнаты":
        save_user_field(user_id, 'game_status', 'idle')
        send_message(chat_id, "Ты вышел(а) из Комнаты. Возвращайся, когда будешь готов(а)!")
        show_main_menu(chat_id, "Главное меню:")
        return
    
    if text == "🔙 Назад":
        if day > 1:
            show_game_question(chat_id, user_id, day - 1)
        else:
            send_message(chat_id, "Это первый день, назад нельзя.")
        return
    
    if text == "📝 Ответить":
        send_message(chat_id, "Напиши свой ответ текстом. Я жду.")
        return
    
    # Сохраняем ответ
    day_data = get_game_day(day)
    phrase = extract_key_phrase(text)
    
    answers = user['game_answers']
    answers.append(text)
    save_user_field(user_id, 'game_answers', answers)
    
    phrases = user['key_phrases']
    phrases.append(phrase)
    save_user_field(user_id, 'key_phrases', phrases)
    
    next_day = day + 1
    
    # Сохраняем дату завершения дня
    today_str = date.today().isoformat()
    save_user_field(user_id, 'last_day_completed_date', today_str)
    
    if next_day > 7:
        save_user_field(user_id, 'game_status', 'completed')
        room_text = build_room(user)
        send_message(chat_id,
            f"🎉 *Поздравляю!*\n\n"
            f"Ты прошёл(ла) все 7 дней!\n\n"
            f"{day_data['response']}\n\n"
            f"{room_text}"
        )
        show_main_menu(chat_id, "Главное меню:")
    else:
        save_user_field(user_id, 'game_day', next_day)
        save_user_field(user_id, 'waiting_for_practice', 1)
        save_user_field(user_id, 'game_status', 'idle')
        send_message(chat_id,
            f"✅ *День {day} завершён!*\n\n"
            f"{day_data['response']}\n\n"
            f"📅 *Завтра — День {next_day}.*\n"
            f"Чтобы открыть следующий день, выполни практику из расписания и нажми '✅ Выполнил(а) практику'.\n"
            f"Новый день откроется только завтра.\n"
            f"А пока нажми кнопку ниже, когда выполнишь практику:",
            reply_markup=json.dumps({
                'keyboard': [[{'text': '✅ Выполнил(а) практику'}]],
                'resize_keyboard': True,
                'one_time_keyboard': True
            })
        )
        # Показываем главное меню после этого
        show_main_menu(chat_id, "Главное меню:")

def show_room(chat_id, user):
    room_text = build_room(user)
    send_message(chat_id, room_text)

# =============================================
# CRON для напоминаний
# =============================================
@app.route('/cron', methods=['GET'])
def cron():
    # Защита секретным ключом
    key = request.args.get('key')
    if key != CRON_KEY:
        return "Forbidden", 403
    
    try:
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM users").fetchall()
        conn.close()
        
        for row in rows:
            user = dict(row)
            user_id = user['user_id']
            chat_id = user_id
            archetype = user.get('archetype')
            if not archetype:
                continue
            
            # Напоминания по расписанию
            for period, field in [("morning", "reminder_morning"), ("day", "reminder_day"), ("evening", "reminder_evening")]:
                reminder_time = user.get(field)
                if reminder_time and reminder_time == current_time:
                    title, practice, affirmation = get_practice_text(archetype, period)
                    if title:
                        emoji = {"morning": "🌅", "day": "☀️", "evening": "🌙"}[period]
                        send_message(chat_id,
                            f"{emoji} *{title}*\n\n"
                            f"📌 {practice}\n\n"
                            f"💬 _{affirmation}_"
                        )
                    # Можно добавить флаг, чтобы не дублировать, но пока оставим
            
            # Добавленные задачи
            custom_tasks = user.get('custom_tasks', [])
            for task in custom_tasks:
                if task.get('time') == current_time:
                    send_message(chat_id,
                        f"⏰ *Напоминание*\n\n{task['text']}"
                    )
        
        return "OK", 200
    except Exception as e:
        logging.error(f"Cron error: {e}")
        return "Error", 500

# =============================================
# ЗАПУСК
# =============================================
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
