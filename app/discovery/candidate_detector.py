import re
from typing import Any
from bs4 import BeautifulSoup, Tag
from app.extraction.normalizer import (
    clean_text,
    extract_price_and_currency,
    is_likely_phone_number,
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


NON_TITLE_PHRASES = {
    "sponsored",
    "ad",
    "sponsored ad",
    "sponsored product",
    "featured",
    "brand store",
    "visit the store",
    "brands related to your search",
    "results",
    "best seller",
    "amazon's choice",
    "deal of the day",
}

DISCARDED_TITLE_CLASSES = [
    "a-badge-label",
    "puis-sponsored-label-text",
    "s-label-popover-hover",
    "a-badge-text",
    "s-sponsored-label-info-icon",
]


def _clean_heading_for_title(heading: Tag) -> str:
    clone = BeautifulSoup(str(heading), "html.parser").find()
    if not isinstance(clone, Tag):
        return clean_text(heading.get_text())

    for cls in DISCARDED_TITLE_CLASSES:
        for noise in clone.find_all(True, class_=cls):
            noise.decompose()

    return clean_text(clone.get_text())


def extract_title_and_url_from_card(card: Tag, base_url: str) -> tuple[str, str | None]:
    candidates: list[tuple[int, int, str, str | None]] = []

    # Priority 3: Amazon / standard e-commerce specific title container
    for sel in (
        "h2 a.a-link-normal span",
        "h2 a.a-link-normal",
        "h2.a-size-mini a",
        "h2 a span",
        "h2 a",
        "[data-cy='title-recipe'] h2 a",
        ".s-title-instructions-style h2 a",
    ):
        for el in card.select(sel):
            anchor = el if el.name == "a" else (el.find("a") or el.find_parent("a"))
            text = clean_text(el.get_text())
            link = normalize_url(base_url, anchor.get("href")) if anchor and anchor.get("href") else None
            if text and text.lower() not in NON_TITLE_PHRASES and not is_boilerplate_title(text):
                candidates.append((3, len(text), text, link))

    # Priority 2: Any heading tag with an anchor
    for heading_tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        for heading in card.find_all(heading_tag):
            text = _clean_heading_for_title(heading)
            if not text or text.lower() in NON_TITLE_PHRASES or is_boilerplate_title(text):
                continue
            anchor = heading.find("a") or heading.find_parent("a")
            link = normalize_url(base_url, anchor.get("href")) if anchor and anchor.get("href") else None
            if anchor and link:
                candidates.append((2, len(text), text, link))
            else:
                candidates.append((1, len(text), text, None))

    # Priority 0: Descriptive anchor text
    if not candidates:
        for a in card.find_all("a", href=True):
            text = clean_text(a.get_text())
            link = normalize_url(base_url, a.get("href"))
            if text and len(text) >= 10 and text.lower() not in NON_TITLE_PHRASES and not is_boilerplate_title(text):
                candidates.append((0, len(text), text, link))

    # Sort candidates by priority desc, then text length desc
    if candidates:
        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        best_prio, best_len, best_text, best_link = candidates[0]
        if not best_link:
            first_link = card.find("a", href=True)
            best_link = normalize_url(base_url, first_link.get("href")) if first_link else None
        return best_text, best_link

    # Last resort: anchor with descriptive image alt text (>= 15 chars)
    for a in card.find_all("a", href=True):
        img = a.find("img")
        if img:
            alt = clean_text(img.get("alt") or img.get("title"))
            if alt and len(alt) >= 15 and alt.lower() not in NON_TITLE_PHRASES and not is_boilerplate_title(alt):
                link = normalize_url(base_url, a.get("href"))
                return alt, link

    first_link = card.find("a", href=True)
    fallback_link = normalize_url(base_url, first_link.get("href")) if first_link else None
    return "", fallback_link


def extract_product_id(card: Tag, url: str | None) -> str | None:
    for attr in ("data-asin", "data-product-id", "data-pid", "data-sku", "data-id", "data-item-id"):
        val = card.get(attr)
        if val:
            return str(val).strip()

    if url:
        match_id = re.search(r"[?&](?:pid|productId|id|sku)=([^&#]+)", url)
        if match_id:
            return match_id.group(1)

        match_dp = re.search(r"/(?:dp|product|p|item)/([A-Za-z0-9_-]+)", url)
        if match_dp:
            return match_dp.group(1)

    return None


def extract_card_prices(card: Tag) -> tuple[float | None, float | None, str | None]:
    mrp: float | None = None
    mrp_currency: str | None = None

    # 1. Check for MRP / Strikethrough price
    mrp_elements = card.select(".a-price.a-text-price, .a-text-price, .a-text-strike, del, s, strike, [data-a-strike='true']")
    for el in mrp_elements:
        offscreen = el.select_one(".a-offscreen")
        raw = offscreen.get_text() if offscreen else el.get_text()
        p, c = extract_price_and_currency(raw)
        if p is not None:
            mrp = p
            mrp_currency = c
            break

    if mrp is None:
        for span in card.find_all(["span", "div", "p"]):
            text = clean_text(span.get_text())
            if any(marker in text.lower() for marker in ("m.r.p", "mrp", "list price", "was:")):
                p, c = extract_price_and_currency(text)
                if p is not None:
                    mrp = p
                    mrp_currency = c
                    break

    # 2. Check for Selling price
    selling_price: float | None = None
    selling_currency: str | None = None

    selling_elements = card.select(".a-price:not(.a-text-price)")
    for el in selling_elements:
        offscreen = el.select_one(".a-offscreen")
        if offscreen:
            raw = offscreen.get_text()
            p, c = extract_price_and_currency(raw)
            if p is not None:
                selling_price = p
                selling_currency = c
                break

        whole = el.select_one(".a-price-whole")
        if whole:
            w_text = whole.get_text().strip().rstrip(".,")
            frac = el.select_one(".a-price-fraction")
            f_text = frac.get_text().strip() if frac else ""
            raw = f"₹{w_text}.{f_text}" if f_text else f"₹{w_text}"
            p, c = extract_price_and_currency(raw)
            if p is not None:
                selling_price = p
                selling_currency = c
                break

    if selling_price is None:
        candidates = card.find_all(
            lambda t: t.name in ("span", "div", "p", "b", "strong")
            and any(k in " ".join(t.get("class", [])) for k in ("price", "cost", "offer", "amount", "rate", "sale", "current"))
        )
        if not candidates:
            candidates = card.find_all(["span", "div", "p", "b", "strong"])

        regular_prices: list[tuple[float, str | None]] = []
        for el in candidates:
            if el.name in STRIKE_THROUGH_TAGS or el.find_parent(list(STRIKE_THROUGH_TAGS)) or "a-text-price" in el.get("class", []):
                continue
            text = el.get_text()
            p, c = extract_price_and_currency(text)
            if p is not None:
                regular_prices.append((p, c))

        if regular_prices:
            if mrp is not None:
                valid_candidates = [p for p, _ in regular_prices if p <= mrp]
                if valid_candidates:
                    selling_price = max(valid_candidates)
                    selling_currency = next((c for p, c in regular_prices if p == selling_price and c), None)
            if selling_price is None:
                selling_price = regular_prices[0][0]
                selling_currency = regular_prices[0][1]

    currency = selling_currency or mrp_currency
    if selling_price is not None and mrp is not None and selling_price > mrp:
        selling_price, mrp = mrp, selling_price

    return selling_price, mrp, currency


def extract_card_rating_and_reviews(card: Tag) -> tuple[float | None, int | None]:
    rating: float | None = None
    reviews: int | None = None

    # Rating
    icon_alt = card.select_one("span.a-icon-alt, i.a-icon-star span, i.a-icon-star-small span")
    if icon_alt:
        rating = parse_rating(icon_alt.get_text())

    if rating is None:
        rating_elem = card.select_one("[aria-label*='out of 5'], [aria-label*='stars'], [aria-label*='star']")
        if rating_elem:
            rating = parse_rating(str(rating_elem.get("aria-label")))

    if rating is None:
        for el in card.select("[class*='rating'], [class*='star'], [class*='score'], [class*='rate'], [itemprop='ratingValue']"):
            text = clean_text(el.get_text())
            if text:
                r = parse_rating(text)
                if r is not None:
                    rating = r
                    break

    # Reviews
    review_anchor = card.select_one("a[href*='#customerReviews'], a[href*='customerReviews']")
    if review_anchor:
        span_inside = review_anchor.select_one("span.s-underline-text, span.a-size-base, span")
        text = span_inside.get_text() if span_inside else review_anchor.get_text()
        reviews = parse_review_count(text)

    if reviews is None:
        for el in card.select("span.s-underline-text, [aria-label*='ratings'], [aria-label*='reviews'], [itemprop='reviewCount'], [itemprop='ratingCount']"):
            label = el.get("aria-label")
            text = str(label) if label else el.get_text()
            rc = parse_review_count(text)
            if rc is not None:
                reviews = rc
                break

    if reviews is None:
        for el in card.select("[class*='review'], [class*='reviews'], [class*='count'], [class*='votes']"):
            rc = parse_review_count(el.get_text())
            if rc is not None:
                reviews = rc
                break

    return rating, reviews


def extract_card_extra(card: Tag, page_type: str) -> dict[str, Any]:
    extra: dict[str, Any] = {}

    if page_type == "ecommerce":
        # Badge
        badge_el = card.select_one("span.a-badge-text, span.a-badge-label, [data-a-badge-color]")
        if badge_el:
            b_text = clean_text(badge_el.get_text())
            if b_text and len(b_text) <= 50 and not is_boilerplate_title(b_text):
                extra["badge"] = b_text

        # Delivery
        delivery_el = card.select_one("span[aria-label*='Delivery'], span[aria-label*='delivery']")
        if delivery_el:
            d_text = clean_text(str(delivery_el.get("aria-label")))
            if d_text:
                extra["delivery"] = d_text
        if "delivery" not in extra:
            for span in card.find_all("span"):
                text = clean_text(span.get_text())
                if re.search(r"\b(?:FREE Delivery|Get it by|Fastest delivery)\b", text, re.IGNORECASE):
                    if len(text) <= 80:
                        extra["delivery"] = text
                        break

        # Offers
        for span in card.find_all("span"):
            text = clean_text(span.get_text())
            if re.fullmatch(r"\(?\s*\d+(?:\.\d+)?%\s*(?:off|discount)\s*\)?", text, re.IGNORECASE):
                extra["offer"] = text
                break
            if re.search(r"\b(?:Save\s+(?:₹|\$|Rs\.?)\s*[0-9,]+)\b", text, re.IGNORECASE):
                extra["offer"] = text
                break

    elif page_type == "generic_listing":
        # 1. Phone number discovery
        tel_link = card.find("a", href=lambda h: bool(h and h.startswith("tel:")))
        if tel_link and tel_link.get("href"):
            raw_tel = tel_link["href"].replace("tel:", "").strip()
            if raw_tel and len(raw_tel) <= 40:
                extra["phone"] = raw_tel

        if "phone" not in extra:
            for el in card.find_all(attrs={"data-phone": True}):
                val = str(el["data-phone"]).strip()
                if val and len(val) <= 40:
                    extra["phone"] = val
                    break

        # 2. Address tag or itemprop address
        addr_tag = card.find("address") or card.select_one("[itemprop='address']")
        if addr_tag:
            addr_text = clean_text(addr_tag.get_text())
            if addr_text and len(addr_text) <= 150:
                extra["address"] = addr_text
                extra["location"] = addr_text

        # 3. Span, div, p, a classes and text markers
        for el in card.find_all(["span", "div", "p", "a"]):
            classes = " ".join(el.get("class", [])).lower()
            text = clean_text(el.get_text())
            if not text or len(text) > 120:
                continue

            # Phone in text or class
            if "phone" not in extra:
                if any(k in classes for k in ("phone", "contact", "mobile", "call")):
                    if is_likely_phone_number(text):
                        extra["phone"] = text

            # Company / Provider
            if any(k in classes for k in ("company", "employer", "provider", "business-name", "author")):
                if "company" not in extra and len(text) <= 80 and not is_boilerplate_title(text):
                    extra["company"] = text

            # Location / Locality / Address
            if any(k in classes for k in ("locality", "address", "location", "city", "area", "street")):
                if "location" not in extra and len(text) <= 100:
                    extra["location"] = text
                    if "address" not in extra:
                        extra["address"] = text

            # Category / Service / Specialty
            if any(k in classes for k in ("category", "service", "specialty", "spec", "tag", "chip", "skill")):
                if "category" not in extra and len(text) <= 80:
                    extra["category"] = text

            # Experience / Years in business
            if re.search(r"\b(?:\d+\+?\s*years?(?:\s+in\s+business)?|serving\s+since\s+\d{4}|established\s+in\s+\d{4})\b", text, re.IGNORECASE):
                if "experience" not in extra:
                    extra["experience"] = text

            # Verified badge
            if re.search(r"\b(?:verified|certified|top\s+rated|authorized)\b", text, re.IGNORECASE) and len(text) <= 30:
                if "badge" not in extra:
                    extra["badge"] = text

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

    # Also detect repeated product cards sharing common search-result markers
    for sel in (
        "div[data-component-type='s-search-result']",
        "div[data-asin]:not([data-asin=''])",
        "div.s-result-item",
    ):
        items = soup.select(sel)
        if len(items) >= 2:
            clusters.append(items)

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
