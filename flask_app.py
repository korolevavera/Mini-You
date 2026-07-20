# -*- coding: utf-8 -*-
import os
import json
import sqlite3
import logging
import re
import time
from flask import Flask, request
import requests

app = Flask(__name__)

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set")

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

def reset_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """UPDATE users 
           SET character_name = 'Мини-Я', 
               archetype = NULL, 
               game_status = 'not_started', 
               test_answers = '[]'
           WHERE user_id = ?""",
        (user_id,)
    )
    conn.commit()
    conn.close()
    logging.info(f"User {user_id} reset")

# =============================================
# ТЕСТ (вопросы без эмодзи для надёжности)
# =============================================
QUICK_TEST = [
    {
        "question": "Когда у тебя свободный вечер, ты скорее...",
        "option_a": "Побудешь дома в тишине",
        "option_b": "Позвонишь другу или выйдешь в люди",
        "a_dim": "I",
        "b_dim": "E"
    },
    {
        "question": "Принимая важное решение, ты опираешься на...",
        "option_a": "Конкретные факты и опыт",
        "option_b": "Интуицию и общую картину",
        "a_dim": "S",
        "b_dim": "N"
    },
    {
        "question": "В трудной ситуации тебе важнее...",
        "option_a": "Найти логичное решение",
        "option_b": "Сохранить гармонию",
        "a_dim": "T",
        "b_dim": "F"
    },
    {
        "question": "Твой подход к делам и планам...",
        "option_a": "Планирую заранее",
        "option_b": "Действую по ситуации",
        "a_dim": "J",
        "b_dim": "P"
    },
    {
        "question": "После насыщенного дня тебе помогает восстановиться...",
        "option_a": "Тихий отдых наедине с собой",
        "option_b": "Разговор с близкими",
        "a_dim": "I",
        "b_dim": "E"
    },
    {
        "question": "Тебе интереснее...",
        "option_a": "Работать с конкретными вещами",
        "option_b": "Исследовать идеи и теории",
        "a_dim": "S",
        "b_dim": "N"
    },
    {
        "question": "Как ты справляешься с дедлайнами?",
        "option_a": "Делаю заранее",
        "option_b": "Работаю в последний момент",
        "a_dim": "J",
        "b_dim": "P"
    },
    {
        "question": "В компании незнакомых людей ты...",
        "option_a": "Быстро вливаешься в разговор",
        "option_b": "Держишься в стороне",
        "a_dim": "E",
        "b_dim": "I"
    },
    {
        "question": "При описании события ты обычно...",
        "option_a": "Точно перечисляешь детали",
        "option_b": "Передаёшь общее впечатление",
        "a_dim": "S",
        "b_dim": "N"
    },
    {
        "question": "При принятии решения ты...",
        "option_a": "Анализируешь все за и против",
        "option_b": "Прислушиваешься к интуиции",
        "a_dim": "T",
        "b_dim": "F"
    },
    {
        "question": "Ты предпочитаешь...",
        "option_a": "Иметь чёткий план на день",
        "option_b": "Импровизировать по ходу",
        "a_dim": "J",
        "b_dim": "P"
    },
    {
        "question": "После долгого общения ты чувствуешь...",
        "option_a": "Прилив энергии и желание продолжать",
        "option_b": "Усталость и потребность в тишине",
        "a_dim": "E",
        "b_dim": "I"
    }
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

def show_submenu(chat_id, text):
    keyboard = {
        'keyboard': [[{'text': '🔙 Назад'}]],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }
    send_keyboard(chat_id, text, keyboard)

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
    
    # СЕКРЕТНАЯ КОМАНДА СБРОСА
    if text == '/reset_me':
        reset_user(user_id)
        send_message(chat_id, "🔄 Аккаунт полностью сброшен! Теперь ты как новый пользователь.\n\nНапиши /start, чтобы начать с приветствия.")
        return

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
            
            welcome_text = (
                f"✅ Имя **{name}** сохранено!\n\n"
                f"Отлично! Теперь у тебя есть спутник — **{name}**.\n\n"
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

    if text.startswith('/'):
        if text == '/start':
            if status == 'testing':
                send_message(chat_id, "Ты проходишь тест! Продолжай отвечать на вопросы.")
                return
            show_main_menu(chat_id, f"Главное меню, {name}:")
        elif text == '/test':
            if status == 'testing':
                send_message(chat_id, "Ты уже проходишь тест! Просто отвечай на вопросы.")
                return
            start_test(chat_id, user_id)
        elif text == '/archetype':
            show_archetype(chat_id, user_id)
        elif text == '/menu':
            show_main_menu(chat_id, f"Главное меню, {name}:")
        else:
            show_main_menu(chat_id, "Неизвестная команда. Используй кнопки меню:")
        return

    if text == "🔙 Назад":
        show_main_menu(chat_id, f"Главное меню, {name}:")
        return

    if text == "🏠 Главная":
        show_main_menu(chat_id, f"Главное меню, {name}:")
        
    elif text == "🧠 Мой Архетип":
        if user.get('archetype'):
            show_archetype(chat_id, user_id)
        else:
            send_message(chat_id, 
                "🧠 Ты ещё не проходил(а) тест.\n\n"
                "Нажми /test, чтобы узнать свой архетип."
            )
            
    elif text == "📋 Расписание":
        if user.get('archetype'):
            show_submenu(chat_id, 
                f"📋 *Расписание для архетипа «{ARCHETYPE_NAMES.get(user['archetype'], '')}»*\n\n"
                "🌅 Утро: Практика и аффирмация\n"
                "☀️ День: Практика и аффирмация\n"
                "🌙 Вечер: Практика и аффирмация\n\n"
                "_(Полная версия появится в следующем обновлении!)_"
            )
        else:
            send_message(chat_id, "Сначала определи свой архетип через 🧠 Мой Архетип")
            
    elif text == "🚪 Комната":
        send_message(chat_id, 
            "🚪 *Комната*\n\n"
            "Это твоё личное пространство для самонаблюдения.\n"
            "Здесь ты будешь проходить 7 дней вопросов к себе.\n\n"
            "_(Функция появится в следующем обновлении!)_"
        )
        
    elif text == "📊 Прогресс":
        show_submenu(chat_id, 
            "📊 *Твой прогресс*\n\n"
            f"🧠 Архетип: {ARCHETYPE_NAMES.get(user.get('archetype'), 'не определён')}\n"
            "🚪 Комната: 0 из 7 дней\n"
            "📋 Расписание: не настроено\n\n"
            "_(Детальный прогресс появится в следующем обновлении!)_"
        )
        
    elif text == "⚙️ Настройки":
        show_submenu(chat_id, 
            "⚙️ *Настройки*\n\n"
            "Здесь можно настроить напоминания и управлять данными.\n\n"
            "_(Функция появится в следующем обновлении!)_"
        )
    
    elif status == 'testing':
        handle_test_answer(chat_id, user_id, user, text)
    
    elif len(text.strip()) >= 2 and not text.startswith('/') and not text in ["🏠 Главная", "🧠 Мой Архетип", "📋 Расписание", "🚪 Комната", "📊 Прогресс", "⚙️ Настройки", "🔙 Назад"]:
        send_message(chat_id, 
            f"Ты уже зарегистрирован(а) как **{name}**.\n\n"
            "Используй кнопки меню для навигации 👇"
        )
        show_main_menu(chat_id, "Главное меню:")
        
    else:
        send_message(chat_id, "Используй кнопки меню 👇")
        show_main_menu(chat_id, "Главное меню:")

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
# ЗАПУСК
# =============================================
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
