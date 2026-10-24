import hashlib
from urllib.parse import urlparse
from bs4 import BeautifulSoup, Tag

NOISE_TAGS = {"script", "style", "noscript", "svg", "path", "iframe", "meta", "link"}


def normalize_hostname(url_or_host: str) -> str:
    cleaned = url_or_host.strip().lower()
    if "://" in cleaned:
        parsed = urlparse(cleaned)
        host = parsed.hostname or ""
    else:
        host = cleaned.split(":")[0].split("/")[0]

    if host.startswith("www."):
        host = host[4:]
    return host


def extract_tag_skeleton(element: Tag, depth: int = 0, max_depth: int = 3) -> str:
    if depth > max_depth or not isinstance(element, Tag):
        return ""

    child_skeletons: list[str] = []
    for child in element.find_all(recursive=False):
        if isinstance(child, Tag) and child.name not in NOISE_TAGS:
            skel = extract_tag_skeleton(child, depth + 1, max_depth)
            if skel:
                child_skeletons.append(skel)

    tag_str = element.name
    attrs_present = sorted([k for k in ("role",) if element.has_attr(k)])
    if attrs_present:
        tag_str += f"[{','.join(attrs_present)}]"

    if child_skeletons:
        return f"{tag_str}({'>'.join(child_skeletons[:4])})"
    return tag_str


def compute_structural_fingerprint(html: str, hostname: str, page_type: str) -> str:
    norm_host = normalize_hostname(hostname)
    soup = BeautifulSoup(html, "html.parser")

    for t in soup.find_all(list(NOISE_TAGS)):
        t.decompose()

    main_container = soup.find(["main", "section", "article"]) or soup.body or soup

    structural_tokens: list[str] = [norm_host, page_type]
    candidate_parents = main_container.find_all(
        ["ul", "ol", "div", "section", "article", "table"], recursive=True
    )

    signatures: list[str] = []
    for parent in candidate_parents:
        direct_children = [c for c in parent.find_all(recursive=False) if isinstance(c, Tag)]
        if len(direct_children) >= 2:
            child_tags = ",".join(c.name for c in direct_children[:6])
            first_skel = extract_tag_skeleton(direct_children[0], max_depth=2)
            signatures.append(f"{parent.name}:{child_tags}:{first_skel}")

    signatures.sort()
    structural_tokens.extend(signatures[:10])

    combined = "|".join(structural_tokens)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]
