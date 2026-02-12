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
SALARY_PATTERN = re.compile(r"R\$\s*[\d\.]+,\d{2}")
VACANCY_PATTERN = re.compile(r"(\d+)\s+vagas?", re.IGNORECASE)
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


def parse_salary(text: str):
    match = SALARY_PATTERN.search(text)
    if not match:
        return None, None
    salary_text = match.group(0)
    normalized = salary_text.replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        return salary_text, float(normalized)
    except ValueError:
        return salary_text, None


def parse_vacancies(text: str):
    match = VACANCY_PATTERN.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def find_official_link(container) -> str | None:
    for anchor in container.find_all("a", href=True):
        href = anchor["href"]
        if "pciconcursos.com.br" in href:
            continue
        if href.startswith("http://") or href.startswith("https://"):
            return href
    urls = re.findall(r"https?://\S+", container.get_text(" ", strip=True))
    if urls:
        return urls[0].rstrip(").,;")
    return None


def extract_contests(html: str, base_url: str, filter_sp: bool) -> list:
    soup = BeautifulSoup(html, "html.parser")
    contests = {}
    for container in soup.select("div.ca"):
        anchor = container.find("a", href=True)
        if not anchor:
            continue
        title = anchor.get_text(" ", strip=True)
        if not title:
            continue
        print(f"DEBUG: Encontrado item {title}")
        text = container.get_text(" ", strip=True)
        parent_text = container.parent.get_text(" ", strip=True) if container.parent else ""
        if filter_sp and not re.search(r"\bSP\b", text, re.IGNORECASE) and not re.search(
            r"\bSP\b", parent_text, re.IGNORECASE
        ):
            continue
        end_date = parse_date(text)
        if not end_date:
            continue
        link = urljoin(base_url, anchor["href"])
        official_link = find_official_link(container)
        vacancies = parse_vacancies(text)
        salary_text, salary_value = parse_salary(text)
        contests[link] = {
            "title": title,
            "link": link,
            "official_link": official_link,
            "end_date": end_date.isoformat(),
            "vacancies": vacancies,
            "salary_text": salary_text,
            "salary_value": salary_value,
            "raw_text": text,
        }
    return list(contests.values())


def send_error_discord(message: str) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK")
    if not webhook:
        print("DISCORD_WEBHOOK não configurado.")
        return
    payload = {
        "embeds": [
            {
                "title": "⚠️ Erro no Sniper de Concursos SP",
                "description": message,
            }
        ]
    }
    response = requests.post(webhook, json=payload, timeout=30)
    if response.status_code >= 400:
        print(f"Falha ao enviar webhook: {response.status_code} {response.text}")


def fetch_page():
    headers = {"User-Agent": "Mozilla/5.0"}
    errors = []
    for url in URLS:
        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.url, response.text
            errors.append(f"{url} -> {response.status_code}")
        except requests.RequestException as exc:
            errors.append(f"{url} -> {exc}")
    message = "Não foi possível acessar o site do PCI. " + " | ".join(errors)
    send_error_discord(message)
    raise RuntimeError(message)


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
            salary_value = item.get("salary_value")
            title = item["title"]
            if isinstance(salary_value, (int, float)) and salary_value > 10000:
                title = f"💰 {title}"
            fields = [{"name": "Inscrições até", "value": end_date, "inline": True}]
            if bancas:
                fields.append(
                    {
                        "name": "Banca",
                        "value": f"🎯 {', '.join(bancas)}",
                        "inline": True,
                    }
                )
            if item.get("vacancies"):
                fields.append(
                    {
                        "name": "Vagas",
                        "value": str(item["vacancies"]),
                        "inline": True,
                    }
                )
            if item.get("salary_text"):
                fields.append(
                    {
                        "name": "Salário",
                        "value": item["salary_text"],
                        "inline": True,
                    }
                )
            if item.get("official_link"):
                fields.append(
                    {
                        "name": "Link oficial",
                        "value": item["official_link"],
                        "inline": False,
                    }
                )
            embeds.append(
                {
                    "title": title,
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
    def build_persisted_item(item: dict) -> dict:
        data = {"title": item["title"], "link": item["link"], "end_date": item["end_date"]}
        if item.get("official_link"):
            data["official_link"] = item["official_link"]
        if item.get("vacancies") is not None:
            data["vacancies"] = item["vacancies"]
        if item.get("salary_text"):
            data["salary_text"] = item["salary_text"]
        if item.get("salary_value") is not None:
            data["salary_value"] = item["salary_value"]
        return data

    updated = cleaned + [build_persisted_item(item) for item in new_items]
    save_data(updated)
    if new_items:
        send_discord(new_items)
    print(f"Novos concursos: {len(new_items)}")


if __name__ == "__main__":
    main()
