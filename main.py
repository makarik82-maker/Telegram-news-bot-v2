import os
import json
import time
import requests
import feedparser
from gigachat import GigaChat
from datetime import datetime

# --- 1. НАСТРОЙКИ И КОНСТАНТЫ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GIGA_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")
OWM_KEY = os.getenv("OWM_API_KEY")
STATE_FILE = "state.json"

RSS_URLS = [
    "https://tass.ru/rss/v2.xml",
    "https://ria.ru/export/rss2/archive/index.xml"
]

# --- 2. РАБОТА С СОСТОЯНИЕМ (ЧТОБЫ ПОМНИТЬ ПОСЛЕДНИЙ ЗАПУСК) ---
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_run": 0}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# --- 3. СБОР НОВОСТЕЙ ---
def get_new_news(last_run_time):
    new_news = []
    for url in RSS_URLS:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            # Преобразуем время публикации в timestamp
            pub_time = time.mktime(entry.published_parsed)
            if pub_time > last_run_time:
                new_news.append({
                    "title": entry.title,
                    "link": entry.link,
                    "summary": entry.get("summary", "")
                })
    return new_news

# --- 4. ПОЛУЧЕНИЕ ПОГОДЫ ---
def get_weather():
    try:
        # Текущая погода
        url_curr = f"http://api.openweathermap.org/data/2.5/weather?q=Moscow&appid={OWM_KEY}&units=metric&lang=ru"
        curr = requests.get(url_curr).json()
        
        # Прогноз на 5 дней (нам нужны первые 24 часа - сегодня, и следующие - завтра)
        url_forecast = f"http://api.openweathermap.org/data/2.5/forecast?q=Moscow&appid={OWM_KEY}&units=metric&lang=ru"
        forecast = requests.get(url_forecast).json()
        
        # Форматируем текущую
        curr_text = f"Сейчас: {curr['main']['temp']}°C, {curr['weather'][0]['description']}, облачность {curr['clouds']['all']}%."
        
        # Форматируем прогноз (берем максимумы и минимумы на сегодня и завтра)
        today_list = forecast['list'][:8] # 8 интервалов по 3 часа = 24 часа
        tomorrow_list = forecast['list'][8:16]
        
        def format_day(day_list, day_name):
            temps = [x['main']['temp'] for x in day_list]
            descs = set([x['weather'][0]['description'] for x in day_list])
            return f"{day_name}: от {min(temps)}°C до {max(temps)}°C, {', '.join(descs)}."

        today_text = format_day(today_list, "Сегодня")
        tomorrow_text = format_day(tomorrow_list, "Завтра")
        
        return f"🌤 ПОГОДА В МОСКВЕ:\n{curr_text}\n{today_text}\n{tomorrow_text}"
    except Exception as e:
        return "🌤 Погода: данные временно недоступны."

# --- 5. ОТПРАВКА В GIGACHAT ---
def process_with_gigachat(news_list, weather_text):
    if not news_list:
        return None
        
    # Формируем текст новостей для промпта
    news_text_for_prompt = ""
    for i, news in enumerate(news_list[:25]): # Берем максимум 25 свежих, чтобы не превысить лимит токенов
        news_text_for_prompt += f"{i+1}. {news['title']} (Ссылка: {news['link']})\n"

    prompt = f"""Ты — строгий и профессиональный редактор новостного Telegram-канала. 
Вот список свежих новостей:
{news_text_for_prompt}

Вот данные о погоде:
{weather_text}

ЗАДАЧА:
1. Выбери из списка 10 самых важных и интересных новостей.
2. Перепиши их своими словами (сделай рерайт), чтобы текст был уникальным.
3. К каждой новости добавь 1-2 предложения справочной информации (контекст, почему это важно, предыстория).
4. Обязательно оставь ссылку на источник в конце каждой новости.
5. В самом конце поста добавь блок с погодой.
6. Используй эмодзи для заголовков, но не перебарщив. Форматируй текст для Telegram (жирный шрифт для заголовков).

Верни ТОЛЬКО готовый текст поста, без лишних вступлений вроде "Вот ваш пост"."""

    # Подключаемся к GigaChat
    with GigaChat(credentials=GIGA_CREDENTIALS, verify_ssl=False, scope="GIGACHAT_API_PERS") as giga:
        response = giga.chat(prompt)
        return response.choices[0].message.content

# --- 6. ОТПРАВКА В TELEGRAM ---
def send_to_telegram(text):
    if not text:
        print("Нет новостей для отправки.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Telegram имеет лимит 4096 символов. Если текст длинный, режем его.
    while len(text) > 0:
        chunk = text[:4090]
        text = text[4090:]
        
        payload = {
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML" # GigaChat иногда использует Markdown, но HTML надежнее. 
            # Примечание: если GigaChat выдаст ошибку форматирования, можно убрать parse_mode.
        }
        requests.post(url, data=payload)
        time.sleep(1) # Небольшая пауза, чтобы Telegram не заблокировал за спам

# --- ГЛАВНАЯ ФУНКЦИЯ ---
def main():
    print("Запуск скрипта...")
    state = load_state()
    last_run = state["last_run"]
    current_time = time.time()
    
    print("Сбор новостей...")
    new_news = get_new_news(last_run)
    print(f"Найдено {len(new_news)} новых новостей.")
    
    if not new_news:
        print("Новых новостей нет. Выход.")
        # Все равно обновим время, чтобы не спамить API
        state["last_run"] = current_time
        save_state(state)
        return

    print("Получение погоды...")
    weather = get_weather()
    
    print("Обработка в GigaChat (это может занять минуту)...")
    final_post = process_with_gigachat(new_news, weather)
    
    print("Отправка в Telegram...")
    send_to_telegram(final_post)
    
    # Сохраняем время текущего запуска
    state["last_run"] = current_time
    save_state(state)
    print("Готово!")

if __name__ == "__main__":
    main()
