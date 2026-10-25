import asyncio
from typing import Optional
from playwright.async_api import BrowserContext, Page, Route, TimeoutError as PlaywrightTimeoutError
from app.browser.playwright_manager import playwright_manager
from app.config import settings
from app.security.url_validation import URLValidationError, validate_url

BOT_CHALLENGE_TITLE_KEYWORDS = (
    "just a moment...",
    "attention required! | cloudflare",
    "access denied",
    "robot check",
    "security check",
    "shieldsquare captcha",
)

BOT_CHALLENGE_BODY_SNIPPETS = (
    "cf-turnstile",
    "g-recaptcha",
    "hcaptcha",
    "challenge-running",
    "verify you are human",
    "enable javascript and cookies to continue",
)

CONSENT_BUTTON_SELECTORS = [
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Agree')",
    "button:has-text('Allow all')",
    "#onetrust-accept-btn-handler",
]


class NavigationError(RuntimeError):
    pass


class PageLoadTimeoutError(NavigationError):
    pass


class BotChallengeDetectedError(NavigationError):
    pass


class AccessRestrictedError(NavigationError):
    pass


class EmptyPageError(NavigationError):
    pass


ACCESS_RESTRICTED_URL_SUBSTRINGS = (
    "/authwall",
    "/login",
    "/checkpoint/",
    "/uas/login",
)

ACCESS_RESTRICTED_TITLE_KEYWORDS = (
    "sign in | linkedin",
    "sign up | linkedin",
    "join linkedin",
    "log in | linkedin",
    "security verification | linkedin",
)

ACCESS_RESTRICTED_BODY_SNIPPETS = (
    "authwall",
    "authwall-join-form",
    "join linkedin to view",
    "sign in to view",
    "sign in to see who you already know",
)


def check_bot_challenges(title: str, content: str) -> None:
    title_lower = title.lower()
    for kw in BOT_CHALLENGE_TITLE_KEYWORDS:
        if kw in title_lower:
            raise BotChallengeDetectedError(f"Bot protection challenge screen detected: '{title}'")

    content_lower = content[:5000].lower()
    for snippet in BOT_CHALLENGE_BODY_SNIPPETS:
        if snippet in content_lower:
            raise BotChallengeDetectedError("Bot protection challenge element detected in page")


def check_access_restrictions(final_url: str, title: str, content: str) -> None:
    url_lower = final_url.lower()
    for sub in ACCESS_RESTRICTED_URL_SUBSTRINGS:
        if sub in url_lower:
            raise AccessRestrictedError(
                f"Page is access-restricted or login-gated: redirected to authentication checkpoint '{sub}'"
            )

    title_lower = title.lower()
    for kw in ACCESS_RESTRICTED_TITLE_KEYWORDS:
        if kw in title_lower:
            raise AccessRestrictedError(
                f"Page is access-restricted or login-gated: title '{title}' indicates authentication requirement"
            )

    content_snippet = content[:5000].lower()
    for snip in ACCESS_RESTRICTED_BODY_SNIPPETS:
        if snip in content_snippet:
            raise AccessRestrictedError(
                f"Page is access-restricted or login-gated: authentication wall element detected ('{snip}')"
            )


async def attempt_dismiss_consent(page: Page) -> None:
    for selector in CONSENT_BUTTON_SELECTORS:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=500):
                await button.click(timeout=1000)
                break
        except Exception:
            continue


async def route_interceptor(route: Route) -> None:
    req_url = route.request.url

    if req_url.startswith(("data:", "blob:", "about:")):
        await route.continue_()
        return

    try:
        # Security requirement: Validate all outbound requests and subresources against restricted addresses to prevent SSRF
        validate_url(req_url)
        await route.continue_()
    except URLValidationError:
        # Security requirement: Abort any request targeting private, loopback, or non-public IP addresses
        await route.abort("blockedbyclient")


async def load_rendered_page(
    url: str,
    timeout_ms: Optional[int] = None,
) -> tuple[str, str]:
    validated_url = validate_url(url)
    effective_timeout = timeout_ms or settings.navigation_timeout_ms

    browser = await playwright_manager.get_browser()
    context: BrowserContext = await browser.new_context(
        user_agent=settings.browser_user_agent,
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=False,
    )

    page: Page = await context.new_page()
    context.on("page", lambda p: asyncio.create_task(p.close()))
    page.on("popup", lambda p: asyncio.create_task(p.close()))

    try:
        await page.route("**/*", route_interceptor)

        try:
            response = await page.goto(
                validated_url,
                wait_until="domcontentloaded",
                timeout=effective_timeout,
            )
        except PlaywrightTimeoutError as e:
            raise PageLoadTimeoutError(f"Navigation timed out after {effective_timeout}ms") from e
        except Exception as e:
            raise NavigationError(f"Failed to load URL {validated_url}: {e}") from e

        if response is None:
            raise NavigationError(f"Empty HTTP response received for {validated_url}")

        final_url = page.url
        try:
            validate_url(final_url)
        except URLValidationError as e:
            raise NavigationError(f"Final redirection target failed security validation: {e}") from e

        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except PlaywrightTimeoutError:
            pass

        await attempt_dismiss_consent(page)

        page_title = await page.title()
        rendered_html = await page.content()

        if not rendered_html or len(rendered_html.strip()) < 100:
            raise EmptyPageError(f"Page at {final_url} returned empty or insufficient content")

        check_bot_challenges(page_title, rendered_html)
        check_access_restrictions(final_url, page_title, rendered_html)

        return rendered_html, final_url

    finally:
        await page.close()
        await context.close()
