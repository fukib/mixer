import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.drmax.cz"
SEARCH_URL = "https://www.drmax.cz/hledani?q=garmin%20instinct%203"

STATE_FILE = Path(".state/last_state.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
}

WATCH_TERMS = [
    "garmin instinct 3 solar tactical 50",
    "garmin instinct 3 solar tactical 45",
    # fallbacky pro případ jiného značení
    "instinct 3 solar tactical 50",
    "instinct 3 solar tactical 45",
    "instinct 3 50 tactical",
    "instinct 3 45 tactical",
]


@dataclass
class ProductHit:
    title: str
    url: str
    price: str = ""
    availability: str = ""


def normalize(text: str) -> str:
    text = text.lower()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def extract_hits(html: str) -> List[ProductHit]:
    soup = BeautifulSoup(html, "html.parser")
    hits: List[ProductHit] = []

    # vezmeme všechny odkazy a zkusíme z nich vytáhnout relevantní produktové karty
    for a in soup.find_all("a", href=True):
        raw_text = " ".join(a.stripped_strings)
        href = a.get("href", "").strip()

        if not raw_text or not href:
            continue

        text = normalize(raw_text)

        if not any(term in text for term in WATCH_TERMS):
            continue

        full_url = urljoin(BASE_URL, href)

        # zkusíme vzít širší kontejner kolem odkazu
        container = a
        for _ in range(4):
            if container.parent is None:
                break
            container = container.parent

        container_text = " ".join(container.stripped_strings)
        container_text_norm = normalize(container_text)

        # cena
        price_match = re.search(r"(\d[\d\s]*[,.]?\d*)\s*kč", container_text_norm, re.IGNORECASE)
        price = price_match.group(0) if price_match else ""

        # dostupnost
        availability = ""
        availability_keywords = [
            "skladem",
            "není skladem",
            "naskladníme",
            "na objednávku",
            "dostupnost neznámá",
            "vyprodáno",
        ]
        for kw in availability_keywords:
            if kw in container_text_norm:
                availability = kw
                break

        hits.append(
            ProductHit(
                title=raw_text.strip(),
                url=full_url,
                price=price,
                availability=availability,
            )
        )

    # deduplikace podle URL
    dedup = {}
    for hit in hits:
        dedup[hit.url] = hit
    return list(dedup.values())


def load_previous_state() -> dict:
    if not STATE_FILE.exists():
        return {"hits": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"hits": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_hits(hits: List[ProductHit]) -> str:
    if not hits:
        return "Nic relevantního nenalezeno."
    lines = []
    for hit in hits:
        extra = []
        if hit.price:
            extra.append(f"cena: {hit.price}")
        if hit.availability:
            extra.append(f"dostupnost: {hit.availability}")
        suffix = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"- {hit.title}{suffix}\n  {hit.url}")
    return "\n".join(lines)


def send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets nejsou nastavené, notifikaci neposílám.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": False,
    }
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()


def states_equal(a: dict, b: dict) -> bool:
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
        b, sort_keys=True, ensure_ascii=False
    )


def main() -> int:
    print(f"Fetching: {SEARCH_URL}")
    html = fetch_html(SEARCH_URL)
    hits = extract_hits(html)

    current_state = {
        "search_url": SEARCH_URL,
        "hits": [asdict(hit) for hit in hits],
    }
    previous_state = load_previous_state()

    changed = not states_equal(previous_state, current_state)
    print(f"Found {len(hits)} relevant hit(s). Changed: {changed}")

    if changed:
        save_state(current_state)

        if hits:
            message = (
                "🚨 Garmin Instinct 3 Tactical změna na Dr. Max\n\n"
                f"{format_hits(hits)}\n\n"
                f"Search: {SEARCH_URL}"
            )
        else:
            message = (
                "ℹ️ Změna výsledků na Dr. Max, ale žádný relevantní Tactical Solar model "
                f"momentálně nalezen nebyl.\n\nSearch: {SEARCH_URL}"
            )

        send_telegram(message)
    else:
        print("No change detected.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        raise
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        raise