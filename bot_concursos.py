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
    "https://www.pciconcursos.com.br/concursos/sudeste/sp",
    "https://www.pciconcursos.com.br/concursos/sudeste/",
    "https://www.pciconcursos.com.br/concursos/sp/",
    "https://www.pciconcursos.com.br/concursos/sp",
]

DATA_FILE = "concursos.json"
HASH_FILE = "last_page.hash"

SELECTORS = [
    "div.ca",
    "div.concurso",
    "ul li",
    "table tr",
]

DATE_PATTERN = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
SALARY_PATTERN = re.compile(r"R\$\s*[\d\.]+,\d{2}")
VACANCY_PATTERN = re.compile(r"(\d+)\s+vagas?", re.IGNORECASE)

BANCAS = [
    ("Vunesp", re.compile(r"\bVunesp\b", re.IGNORECASE)),
    ("FGV", re.compile(r"\bFGV\b", re.IGNORECASE)),
    ("FCC", re.compile(r"\bFCC\b", re.IGNORECASE)),
    ("Instituto Mais", re.compile(r"Instituto Mais", re.IGNORECASE)),
]

# ================= LOGGING =================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)

# ================= HTTP SESSION =================


def create_session():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


SESSION = create_session()

# ================= STORAGE =================


def load_data():
    if not os.path.exists(DATA_FILE):
        return []

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_data(items):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


# ================= PARSERS =================


def parse_date(text):
    match = re.search(r"Inscrições até\s*(\d{2}/\d{2}/\d{4})", text, re.I)
    if not match:
        match = DATE_PATTERN.search(text)

    if not match:
        return None

    try:
        return datetime.strptime(match.group(1 if "Inscrições" in match.group(0) else 0), "%d/%m/%Y").date()
    except Exception:
        return None


def parse_salary(text):
    match = SALARY_PATTERN.search(text)
    if not match:
        return None, None

    salary_text = match.group(0)
    normalized = salary_text.replace("R$", "").replace(".", "").replace(",", ".").strip()

    try:
        return salary_text, float(normalized)
    except ValueError:
        return salary_text, None


def parse_vacancies(text):
    match = VACANCY_PATTERN.search(text)
    return int(match.group(1)) if match else None


# ================= EXTRAÇÃO DE CARGOS =================


def extract_positions(contest_url):
    """Busca cargos dentro da página individual do concurso"""
    try:
        time.sleep(random.uniform(1, 2))

        resp = SESSION.get(contest_url, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")

        text = soup.get_text(" ", strip=True)

        match = re.search(
            r"cargo[s]?:\s*(.+?)(?:inscri|remunera|salári|edital)",
            text,
            re.I,
        )

        if not match:
            return []

        raw = match.group(1)

        cargos = [
            c.strip(" ,.;:-")
            for c in re.split(r",|•|-|\n", raw)
            if len(c.strip()) > 3
        ]

        return cargos[:5]

    except Exception as e:
        logger.warning("Erro extraindo cargos: %s", e)
        return []


# ================= HELPERS =================


def normalize_title(title):
    return re.sub(r"\s+", " ", title).strip().lower()


def contest_id(item):
    return normalize_title(item["title"]), item["end_date"]


def detect_bancas(text):
    return [label for label, pattern in BANCAS if pattern.search(text)]


def find_containers(soup):
    for selector in SELECTORS:
        items = soup.select(selector)
        if items:
            logger.info("Usando seletor: %s", selector)
            return items
    return []


def find_official_link(container):
    for anchor in container.find_all("a", href=True):
        href = anchor["href"]
        if "pciconcursos" not in href and href.startswith("http"):
            return href
    return None


# ================= SCRAPER =================


def extract_contests(html, base_url, filter_sp):
    soup = BeautifulSoup(html, "html.parser")
    containers = find_containers(soup)

    contests = {}

    for container in containers:
        anchor = container.find("a", href=True)
        if not anchor:
            continue

        title = anchor.get_text(" ", strip=True)
        text = container.get_text(" ", strip=True)
        parent_text = container.parent.get_text(" ", strip=True) if container.parent else ""
        combined_text = f"{text} {parent_text}"

        if filter_sp and not re.search(r"\bSP\b|São Paulo", combined_text, re.I):
            continue

        end_date = parse_date(combined_text)
        if not end_date or end_date < date.today():
            continue

        link = urljoin(base_url, anchor["href"])

        salary_text, salary_value = parse_salary(combined_text)
        vacancies = parse_vacancies(combined_text)

        contests[link] = {
            "title": title,
            "link": link,
            "official_link": find_official_link(container),
            "end_date": end_date.isoformat(),
            "vacancies": vacancies,
            "salary_text": salary_text,
            "salary_value": salary_value,
            "raw_text": combined_text,
        }

    return list(contests.values())


# ================= DISCORD (LAYOUT PROFISSIONAL) =================


def send_discord(items):
    webhook = os.getenv("DISCORD_WEBHOOK")
    if not webhook:
        return

    items.sort(key=lambda x: x.get("salary_value") or 0, reverse=True)

    for i in range(0, len(items), 10):
        embeds = []

        for item in items[i:i + 10]:

            end_date = datetime.fromisoformat(item["end_date"]).date()
            days_left = (end_date - date.today()).days
            bancas = detect_bancas(item["title"] + item["raw_text"])

            summary = [
                f"📅 {end_date:%d/%m/%Y}",
                f"⏳ {days_left} dias",
            ]

            if item.get("vacancies"):
                summary.append(f"👥 {item['vacancies']} vaga(s)")

            if item.get("salary_text"):
                summary.append(f"💰 {item['salary_text']}")

            if bancas:
                summary.append(f"🎯 {', '.join(bancas)}")

            fields = [{
                "name": "📌 Informações",
                "value": " • ".join(summary),
                "inline": False,
            }]

            if item.get("positions"):
                cargos = "\n".join(f"• {c}" for c in item["positions"])
                fields.append({
                    "name": "🧾 Cargos",
                    "value": cargos[:1000],
                    "inline": False,
                })

            if item.get("official_link"):
                fields.append({
                    "name": "🔗 Link oficial",
                    "value": item["official_link"],
                    "inline": False,
                })

            embeds.append({
                "title": item["title"],
                "url": item["link"],
                "fields": fields,
                "color": 0x2ECC71,
                "footer": {
                    "text": f"Atualizado em {datetime.now():%d/%m/%Y %H:%M}"
                },
            })

        requests.post(webhook, json={"embeds": embeds}, timeout=30)


# ================= FETCH =================


def fetch_page():
    for url in URLS:
        time.sleep(random.uniform(1.5, 3.5))

        response = SESSION.get(url, timeout=30)
        if response.status_code != 200:
            continue

        html = response.text
        new_hash = hashlib.md5(html.encode()).hexdigest()

        if os.path.exists(HASH_FILE):
            with open(HASH_FILE) as f:
                if f.read() == new_hash:
                    logger.info("Página não mudou.")
                    return response.url, None

        with open(HASH_FILE, "w") as f:
            f.write(new_hash)

        return response.url, html

    raise RuntimeError("Falha ao acessar PCI")


# ================= MAIN =================


def main():
    existing = load_data()
    today = date.today()

    cleaned = [
        item for item in existing
        if datetime.fromisoformat(item["end_date"]).date() >= today
    ]

    base_url, html = fetch_page()

    if html is None:
        return

    filter_sp = "sudeste" in base_url and "/sp" not in base_url
    scraped = extract_contests(html, base_url, filter_sp)

    existing_ids = {contest_id(i) for i in cleaned}

    new_items = []
    for item in scraped:
        if contest_id(item) not in existing_ids:
            logger.info("Extraindo cargos: %s", item["title"])
            item["positions"] = extract_positions(item["link"])
            new_items.append(item)

    updated = cleaned + new_items
    save_data(updated)

    if new_items:
        send_discord(new_items)

    logger.info("Novos concursos: %s", len(new_items))


if __name__ == "__main__":
    main()
