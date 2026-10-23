import re
from typing import Any
from bs4 import BeautifulSoup, Tag
from app.extraction.normalizer import (
    clean_text,
    extract_price_and_currency,
    normalize_url,
    parse_rating,
    parse_review_count,
)
from app.extraction.validator import is_boilerplate_title

DISCARDED_TAGS = {"nav", "footer", "header", "aside", "noscript", "style", "script"}
DISCARDED_CLASS_ID_PATTERNS = [
    re.compile(r"\b(?:nav|navigation|menu|footer|header|sidebar|breadcrumb|pagination|banner|promo|advertisement|cookie|modal)\b", re.IGNORECASE)
]

STRIKE_THROUGH_TAGS = {"del", "s", "strike"}


def is_discarded_container(element: Tag) -> bool:
    if element.name in DISCARDED_TAGS:
        return True

    class_list = element.get("class", [])
    classes = " ".join(class_list) if isinstance(class_list, list) else str(class_list)
    element_id = str(element.get("id", ""))
    role = str(element.get("role", "")).lower()

    if role in ("navigation", "banner", "contentinfo"):
        return True

    for pat in DISCARDED_CLASS_ID_PATTERNS:
        if pat.search(classes) or pat.search(element_id):
            return True

    return False


def remove_noise_containers(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(list(DISCARDED_TAGS)):
        tag.decompose()

    for element in soup.find_all(True):
        if element.parent and is_discarded_container(element):
            element.decompose()


def extract_image_from_card(card: Tag, base_url: str) -> str | None:
    for img in card.find_all("img"):
        src = (
            img.get("data-src")
            or img.get("data-original")
            or img.get("data-lazy-src")
            or img.get("src")
        )
        if src and not src.startswith("data:"):
            normalized = normalize_url(base_url, src)
            if normalized:
                return normalized

        srcset = img.get("srcset")
        if srcset:
            parts = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
            if parts:
                normalized = normalize_url(base_url, parts[-1])
                if normalized:
                    return normalized

    return None


def extract_title_and_url_from_card(card: Tag, base_url: str) -> tuple[str, str | None]:
    for heading_tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        for heading in card.find_all(heading_tag):
            text = clean_text(heading.get_text())
            if text and not is_boilerplate_title(text):
                anchor = heading.find("a") or heading.find_parent("a")
                link = normalize_url(base_url, anchor.get("href")) if anchor and anchor.get("href") else None
                if not link:
                    first_link = card.find("a", href=True)
                    link = normalize_url(base_url, first_link.get("href")) if first_link else None
                return text, link

    anchors = card.find_all("a", href=True)
    best_text = ""
    best_link: str | None = None

    for a in anchors:
        text = clean_text(a.get_text())
        if not text:
            img = a.find("img")
            if img:
                text = clean_text(img.get("alt") or img.get("title"))

        if text and not is_boilerplate_title(text) and len(text) > len(best_text):
            best_text = text
            best_link = normalize_url(base_url, a.get("href"))

    if best_text and best_link:
        return best_text, best_link

    first_link = card.find("a", href=True)
    fallback_link = normalize_url(base_url, first_link.get("href")) if first_link else None
    return best_text, fallback_link


def extract_product_id(card: Tag, url: str | None) -> str | None:
    for attr in ("data-product-id", "data-pid", "data-sku", "data-id", "data-item-id"):
        val = card.get(attr)
        if val:
            return str(val)

    if url:
        match_id = re.search(r"[?&](?:pid|productId|id|sku)=([^&#]+)", url)
        if match_id:
            return match_id.group(1)

        match_dp = re.search(r"/(?:dp|product|p|item)/([A-Za-z0-9_-]+)", url)
        if match_dp:
            return match_dp.group(1)

    return None


def extract_card_prices(card: Tag) -> tuple[float | None, float | None, str | None]:
    strike_price: float | None = None
    regular_prices: list[tuple[float, str | None]] = []

    for strike_elem in card.find_all(list(STRIKE_THROUGH_TAGS)):
        text = strike_elem.get_text()
        p, _ = extract_price_and_currency(text)
        if p is not None:
            strike_price = p
            break

    price_elements = card.find_all(
        lambda t: t.name in ("span", "div", "p", "b", "strong")
        and any(k in " ".join(t.get("class", [])) for k in ("price", "cost", "offer", "mrp", "regular", "amount", "rate"))
    )

    if not price_elements:
        price_elements = card.find_all(["span", "div", "p", "b", "strong"])

    for el in price_elements:
        if el.name in STRIKE_THROUGH_TAGS or el.find_parent(list(STRIKE_THROUGH_TAGS)):
            continue
        text = el.get_text()
        price, curr = extract_price_and_currency(text)
        if price is not None:
            regular_prices.append((price, curr))

    if not regular_prices:
        return None, strike_price, None

    currency = next((c for _, c in regular_prices if c is not None), None)

    if strike_price is not None:
        selling_candidate = min(p for p, _ in regular_prices)
        if selling_candidate < strike_price:
            return selling_candidate, strike_price, currency
        return selling_candidate, None, currency

    if len(regular_prices) >= 2:
        sorted_prices = sorted(p for p, _ in regular_prices)
        if sorted_prices[0] < sorted_prices[-1]:
            return sorted_prices[0], sorted_prices[-1], currency
        return sorted_prices[0], None, currency

    return regular_prices[0][0], None, currency


def extract_card_rating_and_reviews(card: Tag) -> tuple[float | None, int | None]:
    rating: float | None = None
    reviews: int | None = None

    for el in card.find_all(["span", "div", "p", "div"]):
        classes = " ".join(el.get("class", []))
        text = el.get_text()
        if not rating and ("rating" in classes or "star" in classes or "★" in text):
            rating = parse_rating(text)
        if not reviews and ("review" in classes or "count" in classes or "(" in text):
            reviews = parse_review_count(text)

    if not rating:
        rating = parse_rating(card.get_text())
    if not reviews:
        reviews = parse_review_count(card.get_text())

    return rating, reviews


def extract_card_extra(card: Tag, page_type: str) -> dict[str, Any]:
    extra: dict[str, Any] = {}

    if page_type == "generic_listing":
        for el in card.find_all(["span", "div", "p"]):
            classes = " ".join(el.get("class", []))
            text = clean_text(el.get_text())
            if not text or len(text) > 80:
                continue

            if "company" in classes or "employer" in classes or "author" in classes:
                if "company" not in extra:
                    extra["company"] = text
            elif "location" in classes or "city" in classes or "address" in classes:
                if "location" not in extra:
                    extra["location"] = text
            elif "category" in classes or "badge" in classes or "tag" in classes:
                if "category" not in extra:
                    extra["category"] = text

    return extra


def parse_candidate_card(card: Tag, base_url: str, page_type: str) -> dict[str, Any] | None:
    title, url = extract_title_and_url_from_card(card, base_url)
    if not title or not url:
        return None

    product_id = extract_product_id(card, url)
    image_url = extract_image_from_card(card, base_url)
    selling_price, mrp, currency = extract_card_prices(card)
    rating, review_count = extract_card_rating_and_reviews(card)
    extra = extract_card_extra(card, page_type)

    return {
        "title": title,
        "url": url,
        "productId": product_id,
        "imageUrl": image_url,
        "sellingPrice": selling_price,
        "mrp": mrp,
        "currency": currency,
        "rating": rating,
        "reviewCount": review_count,
        "extra": extra,
    }


def find_candidate_clusters(soup: BeautifulSoup) -> list[list[Tag]]:
    clusters: list[list[Tag]] = []

    for parent in soup.find_all(["ul", "ol", "div", "section", "main"]):
        children = [c for c in parent.find_all(recursive=False) if isinstance(c, Tag)]
        if len(children) < 2:
            continue

        groups: dict[str, list[Tag]] = {}
        for child in children:
            class_str = ".".join(sorted(child.get("class", [])))
            key = f"{child.name}:{class_str}"
            groups.setdefault(key, []).append(child)

        for group in groups.values():
            if len(group) >= 2:
                clusters.append(group)

    return clusters


def score_cluster(cluster: list[Tag]) -> float:
    if len(cluster) < 2:
        return 0.0

    links_count = 0
    text_lengths: list[int] = []
    headings_count = 0

    for item in cluster:
        if item.find("a", href=True):
            links_count += 1
        text = clean_text(item.get_text())
        text_lengths.append(len(text))
        if item.find(["h1", "h2", "h3", "h4", "h5", "h6"]):
            headings_count += 1

    avg_text_length = sum(text_lengths) / len(text_lengths) if text_lengths else 0
    if avg_text_length < 8:
        return 0.0

    link_ratio = links_count / len(cluster)
    score = (link_ratio * 40.0) + (min(len(cluster), 50) * 1.5) + (headings_count * 2.0)
    return score


def detect_candidate_records(html: str, base_url: str, page_type: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    remove_noise_containers(soup)

    clusters = find_candidate_clusters(soup)
    if not clusters:
        return []

    clusters.sort(key=score_cluster, reverse=True)

    records: list[dict[str, Any]] = []
    for best_cluster in clusters:
        if score_cluster(best_cluster) <= 10.0:
            continue

        cluster_records: list[dict[str, Any]] = []
        for card in best_cluster:
            parsed = parse_candidate_card(card, base_url, page_type)
            if parsed:
                cluster_records.append(parsed)

        if len(cluster_records) >= 2:
            records = cluster_records
            break

    return records
