import requests, json, time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Optional
from fastapi import Request, HTTPException, status
from app.config import settings


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


class TokenManager:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.token_data: Optional[Dict] = None
        self.last_refresh_time: float = 0
        self.refresh_interval: int = 600  # 10 минут

    def _get_new_token(self) -> Optional[Dict]:
        """Запрашивает новый токен у внешнего API ВМТП"""
        token_url = f'https://pp.vmtp.ru/api/token?username={self.username}&password={self.password}'

        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
        }
        try:
            response = requests.post(
                token_url, 
                headers=headers, 
                timeout=10,
                verify=False # Игнорируем проблему с сертификатами Минцифры
            )
            if response.status_code == 200:
                token_data = response.json()
                return {
                    'access_token': token_data['access_token'],
                    'expires': time.time() + 600
                }
            
            print(f"[VMTP AUTH ERROR] Код: {response.status_code}, Ответ: {response.text}")
            return None
            
        except requests.RequestException as e:
            print(f"[VMTP CONNECTION ERROR] Исключение: {e}")
            return None

    def _is_token_valid(self) -> bool:
        if not self.token_data:
            return False
        return time.time() < self.token_data['expires']

    def _refresh_if_needed(self):
        current_time = time.time()
        # Если токена нет или он просрочен
        if not self._is_token_valid():
            self.token_data = self._get_new_token()
            self.last_refresh_time = current_time
        # Либо если подошел интервал обновления
        elif current_time - self.last_refresh_time > self.refresh_interval:
            self.token_data = self._get_new_token()
            self.last_refresh_time = current_time

    def get_valid_token(self) -> Optional[str]:
        """Возвращает строку токена или None в случае ошибки"""
        self._refresh_if_needed()
        if self.token_data:
            return self.token_data['access_token']
        return None


# Инициализируем один глобальный менеджер токенов для всего приложения
token_manager = TokenManager(username=settings.VMTP_USER, password=settings.VMTP_PASSWORD)

def get_vmtp_demands(container: str) -> Optional[Dict]:
    """Запрашивает требования таможни по номеру контейнера за последние 30 дней"""
    token = token_manager.get_valid_token()
    if not token:
        return {"error": "Не удалось авторизоваться во внешней системе ВМТП"}

    current_date = date.today().strftime('%Y-%m-%d')
    past_date = (date.today() - timedelta(days=30)).strftime('%Y-%m-%d')

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
    }
    
    # offset выставил в 0, чтобы искать с самого начала списка, query — наш контейнер
    params = {
        'date-from': past_date, 
        'date-to': current_date, 
        'offset': '20', 
        'query': container
    }
    
    try:
        url = 'https://pp.vmtp.ru/api/remote/erp/api/v2/customs-requirements'
        response = requests.get(url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            return response.json()
        return {"error": f"Ошибка ВМТП: API вернул статус {response.status_code}"}
    except requests.RequestException as e:
        return {"error": f"Ошибка подключения к сервису ВМТП: {str(e)}"}


def format_vmtp_date(raw_date_str: str) -> str:
    """
    Железобетонный парсер дат для ВМТП.
    Превращает 2026-06-23T09:35:43 в "23.06.2026 в 09:35"
    а 2026-06-24T00:00:00 в "24.06.2026"
    """
    if not raw_date_str:
        return '—'
        
    date_str = str(raw_date_str).strip()
    
    # 1. Если пришли пустые нули времени, сразу срезаем их до чистой даты YYYY-MM-DD
    if 'T00:00' in date_str or ' 00:00' in date_str:
        date_str = date_str.split('T')[0].split(' ')[0]
        
    try:
        # 2. Если в строке осталось реальное время (например, T09:35:43)
        if 'T' in date_str:
            # Берём только YYYY-MM-DDTHH:MM:SS (первые 19 символов), отрезая милисекунды и Z
            clean_iso = date_str.replace('Z', '')[:19]
            dt = datetime.datetime.fromisoformat(clean_iso)
            return dt.strftime('%d.%m.%Y в %H:%M')
            
        # 3. Если это чистая дата без времени (YYYY-MM-DD)
        else:
            clean_date = date_str[:10] 
            dt = datetime.datetime.strptime(clean_date, '%Y-%m-%d')
            return dt.strftime('%d.%m.%Y')
            
    except Exception:
        # Страховка: если парсинг не удался, возвращаем строку как есть
        return date_str

def render_demands_table(vmtp_data: dict, container: str) -> str:
    """Генерирует готовый HTML-код таблицы результатов для HTMX"""
    
    # 1. Извлекаем список из правильного ключа ВМТП
    records = []
    if isinstance(vmtp_data, dict):
        data_block = vmtp_data.get('data', {})
        if isinstance(data_block, dict):
            records = data_block.get('customsRequirements', [])

    # 2. Если требований нет
    if not records:
        return f"""
        <h3 class="text-lg font-semibold text-gray-900 mb-3">Требования таможни по терминалу ВМТП</h3>
        <div class="p-4 mb-4 text-sm text-amber-800 rounded-lg bg-amber-50 border border-amber-200">
            Требования по контейнеру <strong>{container}</strong> за последние 30 дней не найдены.
        </div>
        """
        
    # 3. Начинаем сборку таблицы
    html_content = f"""
    <div class="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
        <h3 class="text-lg font-semibold text-gray-800 mb-4">Результаты из ВМТП для {container}</h3>
        <div class="overflow-x-auto">
            <table class="w-full text-sm text-left text-gray-500">
                <thead class="text-xs text-gray-700 uppercase bg-gray-50">
                    <tr>
                        <th class="px-4 py-3">Коносамент (B/L)</th>
                        <th class="px-4 py-3">Тип</th>
                        <th class="px-4 py-3">Детали досмотра</th>
                        <th class="px-4 py-3">Статус</th>
                    </tr>
                </thead>
                <tbody>
    """
    
    for record in records:
        if not isinstance(record, dict):
            continue
            
        status = record.get('status', 'Получено')
        bill_of_lading = record.get('billOfLanding', record.get('billOfLading', '—'))
        
        screening_type = record.get('screeningType')
        screening_type_display = screening_type if screening_type else '—'
        
        # --- 4. ПАРСИНГ ДАТ ПО НОВЫМ ПРАВИЛАМ ---
        
        # Дата издания требования (строго поле 'date' из корня объекта требования)
        formatted_issue_date = format_vmtp_date(record.get('date'))
        
        # Дата досмотра (поле 'screeningDate')
        formatted_screen_date = format_vmtp_date(record.get('screeningDate'))

        # --- 5. ОБРАБОТКА ПРОЦЕНТА ВЫЕМКИ ---
        screening_deep = record.get('screeningDeep')
        if screening_deep in [None, '', 0, '0', 0.0, '0%']:
            deep_display = "без выемки"
        else:
            deep_display = f"{screening_deep}%" if '%' not in str(screening_deep) else str(screening_deep)

        # Сборка блока деталей с новыми датами
        screening_info = f"""
        <div class="space-y-0.5 text-xs text-gray-600">
            <div><span class="text-gray-400">Издано:</span> <span class="font-medium text-gray-700">{formatted_issue_date}</span></div>
            <div><span class="text-gray-400">Досмотр:</span> {formatted_screen_date}</div>
            <div><span class="text-gray-400">Выемка:</span> <span class="font-medium text-gray-700">{deep_display}</span></div>
        </div>
        """
        
        html_content += f"""
                    <tr class="bg-white border-b border-gray-100 hover:bg-gray-50 transition align-top">
                        <td class="px-4 py-3 font-mono text-gray-900 font-medium">{bill_of_lading}</td>
                        <td class="px-4 py-3 text-gray-700 font-medium">{screening_type_display}</td>
                        <td class="px-4 py-3">{screening_info}</td>
                        <td class="px-4 py-3">
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-800">
                                {status}
                            </span>
                        </td>
                    </tr>
        """
        
    html_content += """
                </tbody>
            </table>
        </div>
    </div>
    """
    
    return html_content