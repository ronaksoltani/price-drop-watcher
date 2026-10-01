"""Fetch and parse product pages, track price changes, and send alerts."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger(__name__)
USER_AGENT = "PriceDropWatcher/1.0 (educational project)"


def parse_price(raw: str) -> Decimal:
    normalized = raw.replace("\u00a0", " ").strip()
    match = re.search(r"\d[\d,]*(?:\.\d+)?", normalized)
    if not match:
        raise ValueError(f"Could not find a price in: {raw!r}")
    try:
        return Decimal(match.group().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid price value: {raw!r}") from exc


def fetch_product(product: dict[str, Any], session: requests.Session) -> dict[str, Any]:
    response = session.get(
        product["url"],
        timeout=(5, 20),
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    price_node = soup.select_one(product["price_selector"])
    if price_node is None:
        raise ValueError(f"Price selector matched nothing for {product['id']}")
    price_attribute = product.get("price_attribute")
    raw_price = price_node.get(price_attribute) if price_attribute else price_node.get_text(" ", strip=True)
    if not raw_price:
        raw_price = price_node.get_text(" ", strip=True)

    title_node = soup.select_one(product.get("title_selector", "title"))
    title = title_node.get_text(" ", strip=True) if title_node else product["id"]
    image = None
    image_node = soup.select_one(product["image_selector"]) if product.get("image_selector") else None
    if image_node:
        image_value = image_node.get(product.get("image_attribute", "src"))
        if isinstance(image_value, list):
            image_value = image_value[0] if image_value else None
        if image_value:
            image = urljoin(product["url"], str(image_value))

    try:
        target = Decimal(str(product["target_price"]))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid target price for {product['id']}") from exc
    if not target.is_finite() or target < 0:
        raise ValueError(f"Target price for {product['id']} must be a finite non-negative value.")

    return {
        "id": product["id"],
        "title": title,
        "url": product["url"],
        "price": parse_price(str(raw_price)),
        "target": target,
        "currency": product.get("currency", ""),
        "image": image,
    }


def load_state(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"State file is invalid JSON: {path}") from exc
    return value if isinstance(value, dict) else {}


def save_state(path: Path, state: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def send_telegram(message: str, image: str | None = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before sending alerts.")
    endpoint = f"https://api.telegram.org/bot{token}/"
    if image:
        response = requests.post(
            endpoint + "sendPhoto",
            data={"chat_id": chat_id, "photo": image, "caption": message},
            timeout=(5, 20),
        )
    else:
        response = requests.post(
            endpoint + "sendMessage",
            data={"chat_id": chat_id, "text": message, "disable_web_page_preview": False},
            timeout=(5, 20),
        )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram API rejected the alert.")


def check_products(config_path: Path, state_path: Path, once_session: requests.Session | None = None) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    products = config.get("products", [])
    if not products:
        raise ValueError("The watchlist must contain at least one product.")
    state = load_state(state_path)
    session = once_session or requests.Session()

    for product in products:
        try:
            current = fetch_product(product, session)
            previous = state.get(current["id"], {}).get("last_price")
            try:
                previous_price = Decimal(previous) if previous is not None else None
            except InvalidOperation:
                previous_price = None
            price = current["price"]
            target = current["target"]
            currency = current["currency"]
            print(f"{current['title']}: {currency} {price} (target {currency} {target})")

            should_alert = price <= target and (
                previous_price is None or previous_price > target or price < previous_price
            )
            if should_alert:
                message = (
                    f"Price drop: {current['title']}\n"
                    f"Now: {currency} {price} · Target: {currency} {target}\n"
                    f"{current['url']}"
                )
                send_telegram(message, current["image"])
                LOG.info("Alert sent for %s at %s", current["id"], price)

            state[current["id"]] = {"last_price": str(price)}
        except (requests.RequestException, KeyError, ValueError, RuntimeError) as exc:
            LOG.exception("Could not check product %s", product.get("id", "<unknown>"))
            print(f"ERROR {product.get('id', '<unknown>')}: {exc}")
    save_state(state_path, state)
