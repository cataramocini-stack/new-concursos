import json
import os
import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URLS = [
    "https://www.pciconcursos.com.br/concursos/sudeste/sp/",
    "https://www.pciconcursos.com.br/concursos/sudeste/sp",
    "https://www.pciconcursos.com.br/concursos/sp/",
    "https://www.pciconcursos.com.br/concursos/sp",
    "https://www.pciconcursos.com.br/concursos/sudeste/",
]
DATA_FILE = "concursos.json"
DATE_PATTERN = re.compile(r"Inscrições até\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
BANCAS = [
    ("Vunesp", re.compile(r"\bVunesp\b", re.IGNORECASE)),
    ("FGV", re.compile(r"\bFGV\b", re.IGNORECASE)),
    ("FCC", re.compile(r"\bFCC\b", re.IGNORECASE)),
    ("Instituto Mais", re.compile(r"Instituto Mais", re.IGNORECASE)),
]


def load_data() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def save_data(items: list) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as file:
        json.dump(items, file, ensure_ascii=False, indent=2)


def parse_date(text: str):
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d/%m/%Y").date()
    except ValueError:
        return None


def find_container_text(anchor) -> str:
    container = anchor
    for _ in range(6):
        if container is None:
            break
        text = container.get_text(" ", strip=True)
        if "Inscrições" in text:
            return text
        container = container.parent
    return anchor.get_text(" ", strip=True)


def extract_contests(html: str, base_url: str, filter_sp: bool) -> list:
    soup = BeautifulSoup(html, "html.parser")
    contests = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "concursos" not in href:
            continue
        title = anchor.get_text(" ", strip=True)
        if not title:
            continue
        text = find_container_text(anchor)
        if filter_sp and not re.search(r"\bSP\b", text, re.IGNORECASE) and "São Paulo" not in text:
            continue
        end_date = parse_date(text)
        if not end_date:
            continue
        link = urljoin(base_url, href)
        contests[link] = {
            "title": title,
            "link": link,
            "end_date": end_date.isoformat(),
            "raw_text": text,
        }
    return list(contests.values())


def fetch_page():
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in URLS:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.url, response.text
    response.raise_for_status()


def detect_bancas(text: str) -> list:
    matches = []
    for label, pattern in BANCAS:
        if pattern.search(text):
            matches.append(label)
    return matches


def send_discord(new_items: list) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK")
    if not webhook:
        print("DISCORD_WEBHOOK não configurado.")
        return
    for i in range(0, len(new_items), 10):
        chunk = new_items[i : i + 10]
        embeds = []
        for item in chunk:
            end_date = datetime.fromisoformat(item["end_date"]).strftime("%d/%m/%Y")
            bancas = detect_bancas(f"{item['title']} {item.get('raw_text','')}")
            fields = [{"name": "Inscrições até", "value": end_date, "inline": True}]
            if bancas:
                fields.append(
                    {
                        "name": "Banca",
                        "value": f"🎯 {', '.join(bancas)}",
                        "inline": True,
                    }
                )
            embeds.append(
                {
                    "title": item["title"],
                    "url": item["link"],
                    "fields": fields,
                }
            )
        response = requests.post(webhook, json={"embeds": embeds}, timeout=30)
        if response.status_code >= 400:
            print(f"Falha ao enviar webhook: {response.status_code} {response.text}")


def main() -> None:
    existing = load_data()
    today = date.today()
    cleaned = []
    for item in existing:
        end_date = None
        if isinstance(item, dict):
            end_date_text = item.get("end_date")
            if end_date_text:
                try:
                    end_date = datetime.fromisoformat(end_date_text).date()
                except ValueError:
                    end_date = None
        if end_date is None or end_date >= today:
            cleaned.append(item)
    base_url, html = fetch_page()
    filter_sp = "sudeste" in base_url and "/sp" not in base_url
    scraped = extract_contests(html, base_url, filter_sp)
    existing_links = {item.get("link") for item in cleaned if isinstance(item, dict)}
    new_items = [item for item in scraped if item["link"] not in existing_links]
    updated = cleaned + [
        {key: item[key] for key in ["title", "link", "end_date"]} for item in new_items
    ]
    save_data(updated)
    if new_items:
        send_discord(new_items)
    print(f"Novos concursos: {len(new_items)}")


if __name__ == "__main__":
    main()
