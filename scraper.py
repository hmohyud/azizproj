import time
import random
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

def duckduckgo_search(query, max_sites=3):
    uc_options = uc.ChromeOptions()
    uc_options.add_argument("--no-sandbox")
    uc_options.add_argument("--disable-gpu")
    uc_options.add_argument("--disable-dev-shm-usage")
    uc_options.add_argument("--window-size=1200,1000")
    # uc_options.add_argument("--headless=new")  # Uncomment for headless
    driver = uc.Chrome(options=uc_options)
    links = []
    try:
        driver.get("https://duckduckgo.com/?q=" + query.replace(" ", "+"))
        time.sleep(2)
        elems = driver.find_elements(By.CSS_SELECTOR, "a[data-testid='result-title-a']")
        for e in elems:
            href = e.get_attribute("href")
            if href and href.startswith("http"):
                links.append(href)
            if len(links) >= max_sites:
                break
    finally:
        driver.quit()
    return links

def scrape_page(url, max_len=8000):
    headers = {
        "User-Agent": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      f"AppleWebKit/{random.randint(500,599)}.36 "
                      f"(KHTML, like Gecko) Chrome/11{random.randint(0,99)}.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]): tag.extract()
        text = ' '.join(soup.stripped_strings)
        return text[:max_len]
    except Exception as e:
        return f"Failed to scrape {url}: {e}"

if __name__ == "__main__":
    # ---- Edit these searches as needed ----
    search_queries = [
        "buy 20 pcs P613842",
        "quote for 20 pcs P613842",
        "P613842 equivalent part supplier"
    ]
    
    # NUMBER OF PAGES TO GET PER SEARCH QUERIES (starting with the first result at the top and going down)
    sites_per_query = 3

    for query in search_queries:
        print(f"\n==== DuckDuckGo: {query} ====")
        urls = duckduckgo_search(query, max_sites=sites_per_query)
        if not urls:
            print("No links found.")
            continue
        for idx, url in enumerate(urls, 1):
            print(f"\n[{idx}] {url}")
            page_text = scrape_page(url)
            print(page_text[:2000])  # print the first 2000 chars only
            print("\n--- End of page ---\n")
