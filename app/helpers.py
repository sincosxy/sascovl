import requests, json
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import Request, HTTPException, status


def validate_container_number(number: str) -> bool:
    # Убираем пробелы и тире, приводим к верхнему регистру
    number = number.strip().upper().replace("-", "").replace(" ", "")
    
    if len(number) != 11:
        return False

    # 1. Алфавит для перевода букв в цифры (по стандарту ISO 6346)
    # Обрати внимание: пропущены 11, 22, 33
    char_map = {
        'A': 10, 'B': 12, 'C': 13, 'D': 14, 'E': 15, 'F': 16, 'G': 17, 'H': 18, 'I': 19,
        'J': 20, 'K': 21, 'L': 23, 'M': 24, 'N': 25, 'O': 26, 'P': 27, 'Q': 28, 'R': 29,
        'S': 30, 'T': 31, 'U': 32, 'V': 34, 'W': 35, 'X': 36, 'Y': 37, 'Z': 38
    }

    try:
        sum_val = 0
        # 2. Считаем сумму первых 10 знаков
        for i in range(10):
            char = number[i]
            
            # Если это первые 4 знака (буквы)
            if i < 4:
                val = char_map.get(char)
                if val is None: return False # Ошибка: не буква в префиксе
            # Если это следующие 6 знаков (цифры)
            else:
                if not char.isdigit(): return False
                val = int(char)
            
            # 3. Весовой коэффициент: 2 в степени i
            sum_val += val * (2 ** i)

        # 4. Вычисляем контрольное число
        # Сначала находим остаток от деления на 11
        # По стандарту: делим сумму на 11, отбрасываем дробную часть, умножаем на 11
        check_digit_calculated = sum_val % 11
        
        # Если остаток 10, то контрольное число принимается за 0
        if check_digit_calculated == 10:
            check_digit_calculated = 0
            
        # 5. Сравниваем с последней цифрой номера
        return check_digit_calculated == int(number[10])

    except Exception:
        return False
    
def fit_request(method, url, token, payload=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, params=payload, timeout=10)
        else:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        # Проверяем на HTTP ошибки (4xx, 5xx)
        response.raise_for_status()
        return response.json()
    
    except requests.exceptions.RequestException as e:
        print(f"❌ Ошибка запроса [{method}] {url}: {e}")
        if response := getattr(e, 'response', None):
            print(f"   Ответ сервера: {response.text}")
        return None

def get_schedule(token, date_from, from_loc, to_loc):
    # 1. Получаем ID для локации ОТКУДА
    res_from = fit_request('GET', 'https://api.fesco.com/api/v1/lk/handbooks/locations', token, {"text": from_loc})
    from_id = next((loc['id'] for loc in res_from.get('data', []) if loc['loc_name'] == from_loc), None) if res_from else None

    # 2. Получаем ID для локации КУДА
    res_to = fit_request('GET', 'https://api.fesco.com/api/v1/lk/handbooks/locations', token, {"text": to_loc})
    to_id = next((loc['id'] for loc in res_to.get('data', []) if loc['loc_name'] == to_loc), None) if res_to else None

    # Проверка: нашли ли мы оба ID
    if not from_id or not to_id:
        print(f"⚠️ Не удалось найти ID локаций: {from_loc} -> {from_id}, {to_loc} -> {to_id}")
        return None

    # 3. Запрос расписания
    payload = {
        "beginDate": date_from,
        "routes": [{"beginId": from_id, "finishId": to_id}]
    }
    
    print(f"🔍 Ищем расписание: {from_loc} ({from_id}) -> {to_loc} ({to_id}) на {date_from}...")
    return fit_request('POST', 'https://api.fesco.com/api/v1/lk/schedule/sea', token, payload)


# Регистрируем фильтр локального времени Владивостока
def format_vladivostok_time(dt_utc):
    if not dt_utc:
        return ""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=ZoneInfo("UTC"))
    local_dt = dt_utc.astimezone(ZoneInfo("Asia/Vladivostok"))
    return local_dt.strftime("%d.%m.%Y %H:%M")

def parse_datetime(dt_str: str) -> datetime | None:
    if not dt_str:
        return None
    try:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    
async def verify_auth_cookie(request: Request):
    # Ищем вашу куку, например, "access_token"
    access_token = request.cookies.get("access_token")
    
    if not access_token:
        # Если это HTMX-запрос, можно вернуть специальный заголовок для редиректа на логин
        # Если обычный запрос — кидаем ошибку или редиректим
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Неавторизован"
        )
    
    # Здесь может быть логика проверки токена в БД/Redis
    return access_token