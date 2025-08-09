# scraper_resilient.py
# Async search/scrape with robust fallbacks and optional local LLM relevance.
# pip install "httpx[http2]" beautifulsoup4 trafilatura openai
# Optional: pip install pypdf playwright && playwright install chromium

import asyncio, re, random, time, math, os
from urllib.parse import urlparse, parse_qs, unquote, quote_plus

import httpx
from bs4 import BeautifulSoup
import trafilatura

# ---------- Config ----------
QUERIES = [
    "buy 20 pcs P613842",
    "quote for 20 pcs P613842",
    "P613842 equivalent part supplier",
]
PART_NUMBER = "P613842"

CONCURRENCY_SEARCH = 4
CONCURRENCY_SCRAPE = 10

SEARCH_TIMEOUT  = 8.0
STATIC_TIMEOUT  = 10.0
JS_TIMEOUT_MS   = 2500
MIN_TEXT_LEN    = 450          # below this, try fallback
MAX_PER_QUERY   = 5            # how many links to keep per query

ENABLE_JS_FALLBACK = False     # set True if you really need headless rendering
ENABLE_R_JINA = True           # fast readability proxy for blocked pages (no key)

# Optional SearXNG / Brave / SerpAPI
SEARXNG_BASE = os.getenv("SEARXNG_URL", "").rstrip("/")   # e.g. http://localhost:8080
BRAVE_KEY    = os.getenv("BRAVE_API_KEY", "")
SERPAPI_KEY  = os.getenv("SERPAPI_API_KEY", "")

# Optional local llama.cpp (OpenAI-compatible)
USE_LOCAL_LLM = True
LLAMA_BASE_URL = "http://127.0.0.1:8080/v1"
LLAMA_MODEL    = "local"

try:
    from openai import OpenAI
    client = OpenAI(base_url=LLAMA_BASE_URL, api_key="sk-local")
except Exception:
    client = None
    USE_LOCAL_LLM = False

# ---------- UA / headers ----------
UAS = [
    # rotate a few Chrome strings
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]
def rand_headers():
    return {
        "User-Agent": random.choice(UAS),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }

AD_OR_TRACKER_HOSTS = {
    "duckduckgo.com", "bing.com", "google.com", "doubleclick.net", "g.doubleclick.net",
    "facebook.com", "fb.com", "t.co", "twitter.com",
}

# ---------- Utilities ----------
def decode_ddg_href(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        u = urlparse(href)
        if "duckduckgo.com" in u.netloc and u.path.startswith("/l/"):
            q = parse_qs(u.query)
            if "uddg" in q and q["uddg"]:
                return unquote(q["uddg"][0])
    except Exception:
        pass
    return href

def likely_ad_or_tracker(url: str) -> bool:
    try:
        h = urlparse(url).netloc.lower()
        return any(h == d or h.endswith("."+d) for d in AD_OR_TRACKER_HOSTS)
    except Exception:
        return True

async def extract_main_async(html: str, url: str) -> str:
    return await asyncio.to_thread(
        lambda: trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_recall=True) or ""
    )

async def get_text_with_retries(session: httpx.AsyncClient, url: str, timeout: float, attempts=2) -> str:
    last = None
    for i in range(attempts):
        try:
            r = await session.get(url, timeout=timeout)
            ct = r.headers.get("content-type", "")
            if "pdf" in ct.lower():
                # try PDF extraction if installed
                try:
                    from pypdf import PdfReader
                    import io
                    data = r.content
                    reader = PdfReader(io.BytesIO(data))
                    pages = []
                    for p in reader.pages[:10]:
                        pages.append(p.extract_text() or "")
                    return "\n".join(pages)
                except Exception:
                    return ""
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            await asyncio.sleep(0.2 * (i+1))
    # final fail
    raise last if last else RuntimeError("fetch failed")

# ---------- Search providers ----------
async def ddg_provider(query: str) -> list[str]:
    bases = ("https://duckduckgo.com/html/", "https://html.duckduckgo.com/html/")
    async with httpx.AsyncClient(headers=rand_headers(), follow_redirects=True, http2=True) as s:
        tasks = [asyncio.create_task(_ddg_html(s, b, query)) for b in bases]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    links, seen = [], set()
    for r in results:
        if isinstance(r, list):
            for u in r:
                if u.startswith("http") and not likely_ad_or_tracker(u) and u not in seen:
                    links.append(u); seen.add(u)
    return links

async def _ddg_html(session: httpx.AsyncClient, base: str, query: str) -> list[str]:
    url = f"{base}?q={quote_plus(query)}&kl=us-en"
    try:
        r = await session.get(url, timeout=SEARCH_TIMEOUT)
        if r.status_code not in (200, 304):
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        anchors = soup.select('a[data-testid="result-title-a"]') or soup.select("a.result__a, a.result__url") or soup.find_all("a")
        hrefs = [a.get("href") for a in anchors if a.get("href")]
        decoded = [decode_ddg_href(h) for h in hrefs]
        out, seen = [], set()
        for u in decoded:
            if u.startswith("http") and u not in seen:
                out.append(u); seen.add(u)
        return out
    except Exception:
        return []

async def searxng_provider(query: str) -> list[str]:
    if not SEARXNG_BASE:
        return []
    url = f"{SEARXNG_BASE}/search"
    params = {"q": query, "format": "json"}
    try:
        async with httpx.AsyncClient(headers=rand_headers(), follow_redirects=True, http2=True) as s:
            r = await s.get(url, params=params, timeout=SEARCH_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            out = []
            seen = set()
            for item in data.get("results", []):
                u = item.get("url")
                if u and u.startswith("http") and not likely_ad_or_tracker(u) and u not in seen:
                    out.append(u); seen.add(u)
            return out
    except Exception:
        return []

async def brave_provider(query: str) -> list[str]:
    if not BRAVE_KEY:
        return []
    # Brave Search API v2
    try:
        headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_KEY}
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, http2=True) as s:
            r = await s.get("https://api.search.brave.com/res/v1/web/search", params={"q": query}, timeout=SEARCH_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            out, seen = [], set()
            for item in data.get("web", {}).get("results", []):
                u = item.get("url")
                if u and u.startswith("http") and u not in seen:
                    out.append(u); seen.add(u)
            return out
    except Exception:
        return []

async def serpapi_provider(query: str) -> list[str]:
    if not SERPAPI_KEY:
        return []
    try:
        params = {"engine": "google", "q": query, "api_key": SERPAPI_KEY}
        async with httpx.AsyncClient(follow_redirects=True, http2=True) as s:
            r = await s.get("https://serpapi.com/search.json", params=params, timeout=SEARCH_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            out, seen = [], set()
            for item in data.get("organic_results", []):
                u = item.get("link")
                if u and u.startswith("http") and u not in seen:
                    out.append(u); seen.add(u)
            return out
    except Exception:
        return []

async def search_aggregated(query: str, per_query: int) -> list[str]:
    provs = [ddg_provider, searxng_provider, brave_provider, serpapi_provider]
    tasks = [asyncio.create_task(p(query)) for p in provs]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out, seen = [], set()
    for r in results:
        if isinstance(r, list):
            for u in r:
                if u not in seen and u.startswith("http") and not likely_ad_or_tracker(u):
                    out.append(u); seen.add(u)
                    if len(out) >= per_query:
                        return out
    return out[:per_query]

# ---------- Scraping / Fallbacks ----------
class PlayCtx:
    _lock = asyncio.Lock()
    _play = None
    _browser = None
    _ctx = None

    @classmethod
    async def ensure(cls):
        if not ENABLE_JS_FALLBACK:
            raise RuntimeError("JS fallback disabled")
        async with cls._lock:
            if cls._play is None:
                from playwright.async_api import async_playwright
                cls._play = await async_playwright().start()
                cls._browser = await cls._play.chromium.launch(headless=True)
                cls._ctx = await cls._browser.new_context(
                    user_agent=random.choice(UAS),
                    viewport={"width": 1280, "height": 800},
                    java_script_enabled=True,
                )
                async def _block(route):
                    if route.request.resource_type in {"image","media","font","stylesheet"}:
                        await route.abort()
                    else:
                        await route.continue_()
                await cls._ctx.route("**/*", _block)
            return cls._ctx

    @classmethod
    async def close(cls):
        if cls._ctx:
            await cls._ctx.close(); cls._ctx = None
        if cls._browser:
            await cls._browser.close(); cls._browser = None
        if cls._play:
            await cls._play.stop(); cls._play = None

async def fetch_js(url: str) -> str:
    ctx = await PlayCtx.ensure()
    page = await ctx.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=int(STATIC_TIMEOUT*1000))
        await page.wait_for_timeout(JS_TIMEOUT_MS)
        return await page.content()
    finally:
        await page.close()

async def fetch_r_jina(url: str) -> str:
    # readability proxy via Cloudflare worker
    # supports http/https; we always force http scheme in path
    u = url
    if u.startswith("https://"):
        u = "http://" + u[len("https://"):]
    elif u.startswith("http://"):
        pass
    else:
        u = "http://" + u
    proxy = "https://r.jina.ai/" + u
    async with httpx.AsyncClient(headers=rand_headers(), follow_redirects=True, http2=True) as s:
        r = await s.get(proxy, timeout=STATIC_TIMEOUT)
        if r.status_code in (200, 304) and len(r.text.strip()) > 50:
            return r.text
    return ""

async def smart_scrape(url: str, session: httpx.AsyncClient) -> dict:
    # 1) static
    try:
        html = await get_text_with_retries(session, url, timeout=STATIC_TIMEOUT, attempts=2)
        text = await extract_main_async(html, url)
        if len(text) >= MIN_TEXT_LEN:
            return {"url": url, "source": "static", "text": text}
    except Exception:
        text = ""

    # 2) r.jina.ai readability proxy
    if ENABLE_R_JINA:
        try:
            alt = await fetch_r_jina(url)
            if alt:
                text2 = await extract_main_async(alt, url)
                if len(text2) >= min(200, MIN_TEXT_LEN//2):
                    return {"url": url, "source": "r.jina.ai", "text": text2}
        except Exception:
            pass

    # 3) JS fallback
    if ENABLE_JS_FALLBACK:
        try:
            html = await fetch_js(url)
            text3 = await extract_main_async(html, url)
            if text3:
                return {"url": url, "source": "js", "text": text3}
        except Exception as e:
            return {"url": url, "source": "error", "text": "", "error": str(e)}

    return {"url": url, "source": "static-thin", "text": text}

# ---------- Optional local LLM relevance ----------
async def llm_relevant(text: str, part: str) -> bool:
    if not (USE_LOCAL_LLM and client and text):
        return False
    def _call():
        sys = (f"You classify pages. If the text likely contains supplier / quote / official info for part '{part}' "
               f"or an official equivalent/cross-ref, answer 'YES'. Else 'NO'. One word only.")
        try:
            r = client.chat.completions.create(
                model=LLAMA_MODEL,
                messages=[{"role":"system","content":sys},
                          {"role":"user","content":text[:6000]}],
                max_tokens=3, temperature=0.0)
            return "YES" in (r.choices[0].message.content or "").strip().upper()
        except Exception:
            return False
    return await asyncio.to_thread(_call)

# ---------- Orchestration ----------
async def process_query(query: str, per_query: int, sem_scrape: asyncio.Semaphore):
    print(f"\n==== DuckDuckGo: {query} ====")
    urls = await search_aggregated(query, per_query)
    if not urls:
        print("No links found.")
        return 0, 0, 0

    checked = 0
    scraped_ok = 0
    llm_ok = 0

    async with httpx.AsyncClient(headers=rand_headers(), follow_redirects=True, http2=True) as session:
        async def handle(url: str, idx: int):
            nonlocal checked, scraped_ok, llm_ok
            async with sem_scrape:
                checked += 1
                print(f"\n[{idx}] {url}")
                data = await smart_scrape(url, session)
                text = data.get("text","")
                src  = data.get("source")
                if data.get("source") == "error":
                    print(f"Failed to scrape ({data.get('error')})")
                    return
                if text:
                    scraped_ok += 1
                    print("---- First 1200 chars of extracted text ----")
                    print(text[:1200])
                    print("---- End snippet ----")
                else:
                    print(f"[empty text] source={src}")
                relevant = await llm_relevant(text, PART_NUMBER)
                if relevant:
                    llm_ok += 1
                    print("[LLM] Marked as RELEVANT")
                else:
                    print("[LLM] Marked as not relevant")

        tasks = [asyncio.create_task(handle(u, i)) for i, u in enumerate(urls, 1)]
        for t in asyncio.as_completed(tasks):
            await t

    return checked, scraped_ok, llm_ok

async def main():
    print("Expecting llama.cpp server at", LLAMA_BASE_URL)
    sem_scrape = asyncio.Semaphore(CONCURRENCY_SCRAPE)

    total_checked = total_scraped = total_llm = 0

    # Run query batches with limited concurrency
    for i in range(0, len(QUERIES), CONCURRENCY_SEARCH):
        batch = QUERIES[i:i+CONCURRENCY_SEARCH]
        results = await asyncio.gather(*(process_query(q, MAX_PER_QUERY, sem_scrape) for q in batch))
        for c, s, l in results:
            total_checked += c
            total_scraped += s
            total_llm += l

    print("\n======== OVERALL TALLY ========")
    print("Total pages checked :", total_checked)
    print("Total scraped OK    :", total_scraped)
    print("Total LLM says OK   :", total_llm)
    print("================================")

    if ENABLE_JS_FALLBACK:
        try:
            await PlayCtx.close()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())
