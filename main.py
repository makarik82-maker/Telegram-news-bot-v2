import os
import json
import time
import requests
import feedparser
import urllib3
import httpx
from gigachat import GigaChat
from datetime import datetime

# ОТКЛЮЧАЕМ ВСЕ ПРЕДУПРЕЖДЕНИЯ SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# --- 2. РАБОТА С СОСТОЯНИЕМ ---
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
        url_curr = f"http://api.openweathermap.org/data/2.5/weather?q=Moscow&appid={OWM_KEY}&units=metric&lang=ru"
        curr = requests.get(url_curr, verify=False).json()
        
        url_forecast = f"http://api.openweathermap.org/data/2.5/forecast?q=Moscow&appid={OWM_KEY}&units=metric&lang=ru"
        forecast = requests.get(url_forecast, verify=False).json()
        
        curr_text = f"Сейчас: {curr['main']['temp']}°C, {curr['weather'][0]['description']}, облачность {curr['clouds']['all']}%."
        
        today_list = forecast['list'][:8] 
        tomorrow_list = forecast['list'][8:16]
        
        def format_day(day_list, day_name):
            temps = [x['main']['temp'] for x in day_list]
            descs = set([x['weather'][0]['description'] for x in day_list])
            return f"{day_name}: от {min(temps)}°C до {max(temps)}°C, {', '.join(descs)}."

        today_text = format_day(today_list, "Сегодня")
        tomorrow_text = format_day(tomorrow_list, "Завтра")
        
        return f"🌤 ПОГОДА В МОСКВЕ:\n{curr_text}\n{today_text}\n{tomorrow_text}"
    except Exception as e:
        print(f"Ошибка погоды: {e}")
        return "🌤 Погода: данные недоступны."

# --- 5. ОТПРАВКА В GIGACHAT ---
def process_with_gigachat(news_list, weather_text):
    if not news_list:
        return None
        
    news_text_for_prompt = ""
    for i, news in enumerate(news_list[:25]): 
        news_text_for_prompt += f"{i+1}. {news['title']} (Ссылка: {news['link']})\n"

    prompt = f"""Ты — редактор новостного Telegram-канала. 
Новости:
{news_text_for_prompt}

Погода:
{weather_text}

ЗАДАЧА:
1. Выбери 10 самых важных новостей
2. Сделай рерайт каждой
3. Добавь 1-2 предложения контекста
4. Оставь ссылку на источник
5. В конце добавь погоду
6. Форматируй для Telegram (<b>заголовки</b>)

Верни только готовый пост."""

    # Создаем клиент httpx с отключенной проверкой SSL
    try:
        # Пробуем создать GigaChat с отключенным SSL
        with GigaChat(
            credentials=GIGA_CREDENTIALS, 
            verify_ssl=False, 
            scope="GIGACHAT_API_PERS"
        ) as giga:
            response = giga.chat(prompt)
            return response.choices[0].message.content
    except Exception as e:
        print(f"Ошибка GigaChat: {e}")
        # Если не получилось с GigaChat, пробуем простой вариант
        return f"️ СРОЧНО: Новости за сегодня\n\nОшибка обработки нейросетью: {str(e)[:100]}\n\n{weather_text}"

# --- 6. ОТПРАВКА В TELEGRAM ---
def send_to_telegram(text):
    if not text:
        print("Нет текста для отправки")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    while len(text) > 0:
        chunk = text[:4090]
        text = text[4090:]
        
        payload = {
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML"
        }
        requests.post(url, data=payload, verify=False)
        time.sleep(1)

# --- ГЛАВНАЯ ФУНКЦИЯ ---
def main():
    print("Запуск...")
    state = load_state()
    last_run = state["last_run"]
    current_time = time.time()
    
    print("Сбор новостей...")
    new_news = get_new_news(last_run)
    print(f"Найдено: {len(new_news)}")
    
    if not new_news:
        print("Нет новых новостей")
        state["last_run"] = current_time
        save_state(state)
        return

    print("Погода...")
    weather = get_weather()
    
    print("GigaChat...")
    final_post = process_with_gigachat(new_news, weather)
    
    print("Отправка...")
    send_to_telegram(final_post)
    
    state["last_run"] = current_time
    save_state(state)
    print("Готово!")

if __name__ == "__main__":
    main()
