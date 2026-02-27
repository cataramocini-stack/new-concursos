import json
import os
import re
import time
import random
import logging
import hashlib
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# ================= CONFIG =================

URLS = [
    "https://www.pciconcursos.com.br/concursos/sudeste/sp/",
    "https://www.pciconcursos.com.br/concursos/sudeste/",
    "https://www.pciconcursos.com.br/concursos/sp/",
]

DATA_FILE = "concursos.json"
HASH_FILE = "last_page.hash"

SELECTORS = ["div.ca", "div.concurso"]

DATE_PATTERN = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
SALARY_PATTERN = re.compile(r"R\$\s*[\d\.]+,\d{2}")
VACANCY_PATTERN = re.compile(r"(\d+)\s+vagas?", re.IGNORECASE)

BANCAS = [
    ("Vunesp", re.compile(r"\bVunesp\b", re.IGNORECASE)),
    ("FGV", re.compile(r"\bFGV\b", re.IGNORECASE)),
    ("FCC", re.compile(r"\bFCC\b", re.IGNORECASE)),
    ("Instituto Mais", re.compile(r"Instituto Mais", re.IGNORECASE)),
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ================= HTTP SESSION =================

def create_session():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

SESSION = create_session()

# ================= STORAGE =================

def load_data():
    if not os.path.exists(DATA_FILE): return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except: return []

def save_data(items):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

# ================= PARSERS =================

def parse_date(text):
    match = re.search(r"Inscrições até\s*(\d{2}/\d{2}/\d{4})", text, re.I)
    if not match: match = DATE_PATTERN.search(text)
    if not match: return None
    try:
        dt_str = match.group(1 if "Inscrições" in match.group(0) else 0)
        return datetime.strptime(dt_str, "%d/%m/%Y").date()
    except: return None

def parse_salary(text):
    match = SALARY_PATTERN.search(text)
    if not match: return None, None
    salary_text = match.group(0)
    normalized = salary_text.replace("R$", "").replace(".", "").replace(",", ".").strip()
    try: return salary_text, float(normalized)
    except: return salary_text, None

def parse_vacancies(text):
    match = VACANCY_PATTERN.search(text)
    return int(match.group(1)) if match else None

# ================= EXTRAÇÃO DE CARGOS (REFATORADA) =================

def extract_positions(contest_url):
    """Busca cargos com regex mais restritiva para evitar 'shitcode' no resultado"""
    try:
        time.sleep(random.uniform(1, 2))
        resp = SESSION.get(contest_url, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # O segredo é focar na div central de conteúdo da PCI
        main_content = soup.find('div', id='concurso') or soup.find('div', class_='ca')
        text = main_content.get_text(" ", strip=True) if main_content else soup.get_text(" ", strip=True)

        # Regex focada em capturar o que vem após 'Cargo:' ou 'Vagas para:' 
        # ignorando blocos de 'Inscrições', 'Salário' e links.
        match = re.search(r"(?:Cargos?|Vagas? para):\s*(.+?)(?:\.|Inscri|Remunera|Salári|Edital|Taxa|Prova)", text, re.I | re.S)
        
        if not match: return []

        raw = match.group(1)
        # Split por vírgula ou ponto e vírgula, limpando lixo e removendo strings curtas demais (ruído)
        cargos = [
            c.strip(" ,.;:-") 
            for c in re.split(r",|;|e\s\b|•|\n", raw) 
            if len(c.strip()) > 4 and "http" not in c.lower()
        ]

        return list(dict.fromkeys(cargos))[:5] # Remove duplicatas mantendo ordem
    except Exception as e:
        logger.warning(f"Erro extraindo cargos: {e}")
        return []

# ================= HELPERS =================

def contest_id(item):
    # ID único baseado no link, já que o título pode repetir
    return hashlib.md5(item["link"].encode()).hexdigest()

def detect_bancas(text):
    return [label for label, pattern in BANCAS if pattern.search(text)]

def find_official_link(container):
    for anchor in container.find_all("a", href=True):
        href = anchor["href"]
        if "pciconcursos" not in href and href.startswith("http"):
            return href
    return None

# ================= SCRAPER =================

def extract_contests(html, base_url, filter_sp):
    soup = BeautifulSoup(html, "html.parser")
    containers = []
    for s in SELECTORS:
        found = soup.select(s)
        if found:
            containers = found
            break

    contests = {}
    for container in containers:
        anchor = container.find("a", href=True)
        if not anchor: continue

        title = anchor.get_text(" ", strip=True)
        text = container.get_text(" ", strip=True)
        
        # Filtro geográfico básico
        if filter_sp and not re.search(r"\bSP\b|São Paulo", text, re.I):
            continue

        end_date = parse_date(text)
        if not end_date or end_date < date.today():
            continue

        link = urljoin(base_url, anchor["href"])
        salary_text, salary_value = parse_salary(text)
        
        contests[link] = {
            "title": title,
            "link": link,
            "official_link": find_official_link(container),
            "end_date": end_date.isoformat(),
            "vacancies": parse_vacancies(text),
            "salary_text": salary_text,
            "salary_value": salary_value,
            "raw_text": text,
        }

    return list(contests.values())

# ================= DISCORD =================

def send_discord(items):
    webhook = os.getenv("DISCORD_WEBHOOK")
    if not webhook: return

    items.sort(key=lambda x: x.get("salary_value") or 0, reverse=True)

    for i in range(0, len(items), 10):
        embeds = []
        for item in items[i:i + 10]:
            end_date = datetime.fromisoformat(item["end_date"]).date()
            days_left = (end_date - date.today()).days
            bancas = detect_bancas(item["title"] + item["raw_text"])
            
            # Melhora o título: Se tiver cargo, coloca no título para diferenciar as vagas da UNICAMP
            main_pos = f" - {item['positions'][0]}" if item.get("positions") else ""
            display_title = f"{item['title']}{main_pos}"

            summary = [
                f"📅 {end_date:%d/%m/%Y} ({days_left}d)",
                f"💰 {item.get('salary_text', 'Não informado')}",
                f"👥 {item.get('vacancies') or '?'} vaga(s)"
            ]
            if bancas: summary.append(f"🎯 {', '.join(bancas)}")

            fields = []
            if item.get("positions") and len(item["positions"]) > 1:
                fields.append({
                    "name": "🧾 Outros Cargos",
                    "value": "\n".join(f"• {c}" for c in item["positions"][1:]),
                    "inline": False,
                })

            if item.get("official_link"):
                fields.append({
                    "name": "🔗 Edital/Link Oficial",
                    "value": item["official_link"],
                    "inline": False,
                })

            embeds.append({
                "title": display_title[:250],
                "url": item["link"],
                "description": " • ".join(summary),
                "fields": fields,
                "color": 0x2ECC71,
                "footer": {"text": f"Ref: {contest_id(item)[:8]} | Atualizado: {datetime.now():%H:%M}"}
            })

        try:
            requests.post(webhook, json={"embeds": embeds}, timeout=30)
        except Exception as e:
            logger.error(f"Erro Discord: {e}")

# ================= MAIN =================

def main():
    existing = load_data()
    today = date.today()
    cleaned = [i for i in existing if datetime.fromisoformat(i["end_date"]).date() >= today]
    
    try:
        base_url, html = fetch_page()
    except Exception as e:
        logger.error(e)
        return

    if html is None: return

    filter_sp = "sudeste" in base_url and "/sp" not in base_url
    scraped = extract_contests(html, base_url, filter_sp)
    
    existing_links = {i["link"] for i in cleaned}
    new_items = []

    for item in scraped:
        if item["link"] not in existing_links:
            logger.info(f"New: {item['title']}")
            item["positions"] = extract_positions(item["link"])
            new_items.append(item)

    if new_items:
        save_data(cleaned + new_items)
        send_discord(new_items)
        logger.info(f"Processados {len(new_items)} novos concursos.")

def fetch_page():
    for url in URLS:
        try:
            time.sleep(2)
            resp = SESSION.get(url, timeout=30)
            if resp.status_code != 200: continue
            
            new_hash = hashlib.md5(resp.text.encode()).hexdigest()
            if os.path.exists(HASH_FILE):
                with open(HASH_FILE) as f:
                    if f.read() == new_hash:
                        logger.info(f"Sem alterações em {url}")
                        return resp.url, None

            with open(HASH_FILE, "w") as f: f.write(new_hash)
            return resp.url, resp.text
        except: continue
    raise RuntimeError("PCI Offline ou Bloqueado")

if __name__ == "__main__":
    main()
