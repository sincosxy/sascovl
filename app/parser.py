import io
import re
import urllib.parse
import requests
import pdfplumber
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
import asyncio

from app.models import Base, ProcessedFile, ContainerArchive

from app.db import engine, Base, get_db
from app.models import ProcessedFile, ContainerArchive
from app.helpers import validate_container_number

UNIT_PATTERN = re.compile(r'\b[A-Za-z][\s-]*[A-Za-z][\s-]*[A-Za-z][\s-]*[A-Za-z]\d{7}\b')
DATE_PATTERN = re.compile(r'\b(?:(\d{2})[\./](\d{2})[\./](\d{4})|(\d{4})-(\d{2})-(\d{2}))\b')


TARGET_URLS = {
    "Взвешивание": "https://www.vsct.info/index/klientam/docs/trebovaniya-na-vzveshivanie.html",
    "Янтарь": "https://www.vsct.info/index/klientam/docs/akt-izveshhenie.html",
    "МИДК": "https://www.vsct.info/index/klientam/docs/midk.html",
    "Досмотр": "https://www.vsct.info/index/klientam/docs/trebovaniya-otd.html"
}


def extract_document_date(first_page_text: str, file_name: str):
    """Ищет дату издания в тексте первой страницы или в названии файла."""
    # 1. Сначала ищем в тексте первой страницы
    match = DATE_PATTERN.search(first_page_text)
    
    # 2. Если на странице нет, ищем в названии файла
    if not match:
        match = DATE_PATTERN.search(file_name)
        
    if match:
        day1, month1, year1, year2, month2, day2 = match.groups()
        try:
            if year1: # Формат ДД.ММ.ГГГГ
                return datetime.strptime(f"{day1}.{month1}.{year1}", "%d.%m.%Y").date()
            elif year2: # Формат ГГГГ-ММ-ДД
                return datetime.strptime(f"{year2}-{month2}-{day2}", "%Y-%m-%d").date()
        except ValueError:
            return None # На случай, если дата невалидная (например, 45.13.2026)
            
    return None

def clean_unit_code(raw_code):
    """Очистка кода контейнера."""
    return re.sub(r'[\s-]', '', raw_code).upper()

def get_pdf_links(url):
    """Сканирует страницу, автоматически вычисляет BASE_URL и собирает ссылки."""
    # Автоматически вычисляем базовый домен из переданного URL
    # Например, из 'https://example.com' получится 'https://example.com'
    parsed_base = urllib.parse.urlparse(url)
    computed_base_url = f"{parsed_base.scheme}://{parsed_base.netloc}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при подключении к сайту: {e}")
        return []

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(response.text, 'html.parser')
    pdf_links = []

    for link in soup.find_all('a', href=True):
        href = link['href'].strip()
        if href.lower().endswith('.pdf'):
            # Используем вычисленный базовый URL для относительных ссылок
            full_url = urllib.parse.urljoin(computed_base_url, href) if not href.startswith('http') else href
            
            parsed_url = urllib.parse.urlparse(full_url)
            encoded_path = urllib.parse.quote(parsed_url.path)
            final_url = urllib.parse.urlunparse((
                parsed_url.scheme, parsed_url.netloc, encoded_path,
                parsed_url.params, parsed_url.query, parsed_url.fragment
            ))
            pdf_links.append(final_url)
            
    return list(set(pdf_links))

async def parse_and_save():
    # 1. Создаем таблицы, если их еще нет в Postgres
    #Base.metadata.create_all(engine)
    
    # 2. Получаем сессию базы данных из вашего get_db()
    # closing гарантирует, что сессия закроется правильно после окончания работы
    async for session in get_db():
        
        #pdf_urls = get_pdf_links("https://www.vsct.info/index/klientam/docs/trebovaniya-na-vzveshivanie.html")
        all_pdf_urls = []
        print("🕵️‍♂️ Начинаем обход целевых страниц...")
        for group_name, target_url in TARGET_URLS.items():
            print(f"Сканирую страницу: {group_name}")
            
            # Собираем ссылки с конкретной страницы
            pdf_urls = get_pdf_links(target_url)
            print(f"  -> Найдено PDF: {len(pdf_urls)}")
            
            # Добавляем в общий список
            #all_pdf_urls.extend(site_pdf_links)
        #print(f"Найдено документов: {len(pdf_urls)}")

        #unique_pdf_urls = list(set(all_pdf_urls))
        #print(f"\n Всего уникальных PDF для проверки: {len(unique_pdf_urls)}")

            for url in pdf_urls:
                clean_filename = urllib.parse.unquote(url.split('/')[-1])
                
                # Проверяем, был ли файл обработан ранее
                result = await session.execute(select(ProcessedFile).filter_by(file_name=clean_filename))
                already_processed = result.scalar_one_or_none()
                if already_processed:
                    continue
                    
                print(f"Обработка файла: {clean_filename}...")
                try:
                    response = requests.get(url, timeout=15)
                    response.raise_for_status()
                    
                    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
                        first_page_text = pdf.pages[0].extract_text() or "" if pdf.pages else ""
                        doc_date = extract_document_date(first_page_text, clean_filename)
                        
                        for page_num, page in enumerate(pdf.pages, start=1):
                            tables = page.extract_tables()
                            if not tables: 
                                continue
                                
                            for table in tables:
                                for row in table:
                                    clean_row = [str(cell).strip() if cell is not None else "" for cell in row]
                                    combined_text = " ".join(clean_row)
                                    
                                    all_matches = UNIT_PATTERN.findall(combined_text)
                                    if all_matches:
                                        for raw_unit in list(set(all_matches)):
                                            clean_unit = clean_unit_code(raw_unit)
                                            is_valid = validate_container_number(clean_unit)
                                            
                                            # Сохраняем через родную сессию CRM
                                            record = ContainerArchive(
                                                container_number=clean_unit,
                                                file_name=clean_filename,
                                                page_number=page_num,
                                                raw_row_text=" | ".join(clean_row),
                                                is_valid_iso=is_valid,
                                                document_date=doc_date,
                                                source_group=group_name
                                            )
                                            session.add(record)
                                            
                    # Фиксируем успешную обработку файла
                    session.add(ProcessedFile(file_name=clean_filename))
                    await session.commit()
                    print(f"✅ Успешно добавлен в базу: {clean_filename}")
                    
                except Exception as e:
                    await session.rollback()
                    print(f"❌ Ошибка при обработке {clean_filename}: {e}")

if __name__ == "__main__":
    asyncio.run(parse_and_save())
