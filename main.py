import os
import json
import uuid
import logging
import requests
import feedparser
from pathlib import Path
from datetime import datetime, timezone

# ============================================================
# НАСТРОЙКИ
# ============================================================

# ГЛАВНОЕ РЕШЕНИЕ ПРОБЛЕМЫ SSL:
VERIFY_SSL = False

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")
OWM_API_KEY = os.getenv("OWM_API_KEY")

RSS_URLS = [
    "https://tass.ru/rss/v2.xml",
    "https://ria.ru/export/rss2/archive/index.xml",
]

MAX_NEWS_FOR_POST = 10
PUBLISHED_FILE = Path("published_news.json")
MAX_PUBLISHED_IDS = 1000
HTTP_TIMEOUT = 30

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("newsbot")

# ============================================================
# УПРАВЛЕНИЕ ОПУБЛИКОВАННЫМИ НОВОСТЯМИ
# ============================================================
def load_published_news() -> list:
    if not PUBLISHED_FILE.exists():
        return []
    try:
        with PUBLISHED_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return [str(item) for item in data if item][-MAX_PUBLISHED_IDS:]
    except Exception:
        return []

def save_published_news(news_ids: list):
    unique_ids = list(dict.fromkeys(str(item) for item in news_ids if item))
    unique_ids = unique_ids[-MAX_PUBLISHED_IDS:]
    tmp_file = PUBLISHED_FILE.with_suffix(".tmp")
    try:
        with tmp_file.open("w", encoding="utf-8") as f:
            json.dump(unique_ids, f, ensure_ascii=False, indent=2)
        tmp_file.replace(PUBLISHED_FILE)
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

# ============================================================
# СБОР НОВОСТЕЙ
# ============================================================
def get_rss_news() -> list:
    logger.info("Собираю новости из RSS...")
    published_ids = set(load_published_news())
    candidates = []
    seen_urls = set()

    for rss_url in RSS_URLS:
        try:
            # ВАЖНО: verify=VERIFY_SSL
            response = requests.get(rss_url, timeout=HTTP_TIMEOUT, verify=VERIFY_SSL)
            response.raise_for_status()
            feed = feedparser.parse(response.content)

            for entry in feed.entries[:30]: # Берем последние 30 из каждой ленты
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                description = entry.get("summary", entry.get("description", "")).strip()
                
                # Уникальный идентификатор
                news_id = entry.get("id", link or title)

                if not title or not news_id or link in seen_urls:
                    continue
                
                if news_id in published_ids:
                    continue

                candidates.append({
                    "id": news_id,
                    "title": title,
                    "description": description,
                    "link": link,
                    "source": "ТАСС" if "tass" in rss_url else "РИА Новости"
                })
                seen_urls.add(link)
                
        except Exception as e:
            logger.error(f"Ошибка загрузки RSS {rss_url}: {e}")

    # Сортируем: сначала самые свежие (упрощенно, по порядку в ленте)
    return candidates

# ============================================================
# ПОГОДА (OpenWeatherMap)
# ============================================================
def get_weather() -> str:
    logger.info("Получаю погоду...")
    try:
        # Текущая
        url_curr = f"http://api.openweathermap.org/data/2.5/weather?q=Moscow&appid={OWM_API_KEY}&units=metric&lang=ru"
        curr = requests.get(url_curr, timeout=HTTP_TIMEOUT, verify=VERIFY_SSL).json()
        
        # Прогноз
        url_forecast = f"http://api.openweathermap.org/data/2.5/forecast?q=Moscow&appid={OWM_API_KEY}&units=metric&lang=ru"
        forecast = requests.get(url_forecast, timeout=HTTP_TIMEOUT, verify=VERIFY_SSL).json()
        
        curr_text = f"Сейчас: {curr['main']['temp']}°C, {curr['weather'][0]['description']}, облачность {curr['clouds']['all']}%."
        
        today_list = forecast['list'][:8] 
        tomorrow_list = forecast['list'][8:16]
        
        def format_day(day_list, day_name):
            temps = [x['main']['temp'] for x in day_list]
            descs = set([x['weather'][0]['description'] for x in day_list])
            return f"{day_name}: от {min(temps)}°C до {max(temps)}°C, {', '.join(descs)}."

        return f"🌤 ПОГОДА В МОСКВЕ:\n{curr_text}\n{format_day(today_list, 'Сегодня')}\n{format_day(tomorrow_list, 'Завтра')}"
    except Exception as e:
        logger.error(f"Ошибка погоды: {e}")
        return "🌤 Погода: данные временно недоступны."

# ============================================================
# GIGACHAT (ПРЯМЫЕ ЗАПРОСЫ ЧЕРЕЗ requests)
# ============================================================
def get_gigachat_token() -> str:
    logger.info("Получаю токен GigaChat...")
    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {GIGACHAT_CREDENTIALS}",
    }
    # ВАЖНО: verify=VERIFY_SSL
    response = requests.post(url, headers=headers, data={"scope": "GIGACHAT_API_PERS"}, verify=VERIFY_SSL, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json().get("access_token")

def process_with_gigachat(news_list: list, weather_text: str) -> str:
    if not news_list:
        return None
        
    news_text = "\n".join([f"{i+1}. {n['title']} | {n['description'][:150]}... | Ссылка: {n['link']}" for i, n in enumerate(news_list[:15])])

    prompt = f"""Ты — редактор новостного Telegram-канала.
Новости:
{news_text}

Погода:
{weather_text}

ЗАДАЧА:
1. Выбери 10 самых важных новостей.
2. Сделай рерайт каждой (1 абзац).
3. Добавь 1-2 предложения справочной информации (контекст).
4. В конце каждой новости обязательно оставь: Источник: [ссылка]
5. В самом конце поста добавь блок с погодой.
6. Используй эмодзи и <b>жирный шрифт</b> для заголовков.
Верни ТОЛЬКО готовый текст поста."""

    try:
        token = get_gigachat_token()
        url = "https://api.giga.chat/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload = {
            "model": "GigaChat-3-Ultra",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1500,
        }
        # ВАЖНО: verify=VERIFY_SSL
        response = requests.post(url, headers=headers, json=payload, verify=VERIFY_SSL, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Ошибка GigaChat: {e}")
        # Фолбэк: если нейросеть упала, вернем просто новости и погоду
        return f"⚠️ Ошибка обработки AI: {str(e)[:100]}\n\n{weather_text}"

# ============================================================
# TELEGRAM
# ============================================================
def send_to_telegram(text: str):
    if not text:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Разбиваем на части, если текст длиннее 4000 символов
    for i in range(0, len(text), 4000):
        chunk = text[i:i+4000]
        payload = {"chat_id": CHAT_ID, "text": chunk, "parse_mode": "HTML"}
        # ВАЖНО: verify=VERIFY_SSL
        requests.post(url, data=payload, verify=VERIFY_SSL, timeout=HTTP_TIMEOUT)

# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================
def main():
    logger.info("=== ЗАПУСК БОТА ===")
    
    # 1. Сбор
    candidates = get_rss_news()
    logger.info(f"Найдено {len(candidates)} новых новостей.")
    
    if not candidates:
        logger.info("Новых новостей нет. Выход.")
        return

    # 2. Погода
    weather = get_weather()
    
    # 3. Обработка
    final_post = process_with_gigachat(candidates, weather)
    
    # 4. Отправка
    send_to_telegram(final_post)
    
    # 5. Сохранение ID, чтобы не публиковать дубли
    published = load_published_news()
    published.extend([n["id"] for n in candidates])
    save_published_news(published)
    
    logger.info("=== УСПЕШНО ЗАВЕРШЕНО ===")

if __name__ == "__main__":
    main()
