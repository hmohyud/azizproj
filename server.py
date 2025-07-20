import os
import time
import random
import json
import threading
import uuid

from urllib.parse import urlparse, urljoin
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
import requests

# ======== LOAD SECRETS & SETUP ========
load_dotenv()
client = OpenAI()
app = Flask(__name__)
CORS(app)

# -- Global dict to manage stop tokens (threadsafe) --
STOP_FLAGS = {}

def stoppable_sleep(duration, stream_id, chunk=0.15):
    slept = 0
    while slept < duration:
        if STOP_FLAGS.get(stream_id):
            return True  # Stopped
        time.sleep(min(chunk, duration - slept))
        slept += chunk
    return False

def duckduckgo_search(query, max_sites=3, stream_id=None):
    options = Options()
    # options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1200,1000")
    service = Service("/usr/local/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    links = []
    try:
        driver.get("https://duckduckgo.com/?q=" + query.replace(" ", "+"))
        time.sleep(2)
        if stream_id and STOP_FLAGS.get(stream_id):
            print("[STOP CHECK] Duckduckgo stopped during wait")
            return []
        elems = driver.find_elements(By.CSS_SELECTOR, "a[data-testid='result-title-a']")
        for e in elems:
            if stream_id and STOP_FLAGS.get(stream_id):
                print("[STOP CHECK] Duckduckgo stopped in result parse")
                return []
            href = e.get_attribute("href")
            if href and href.startswith("http"):
                links.append(href)
            if len(links) >= max_sites:
                break
    except Exception as e:
        print(f"[Selenium] DuckDuckGo search error: {e}")
    finally:
        driver.quit()
    return links



def extract_info_and_queries(request_string):
    sys = (
        "Extract all aerospace part numbers and the quantity (if specified) from the following request. "
        "Then, write 3 Google search queries to find quotes or equivalent parts. "
        "If a quantity is specified, include it in the queries (e.g., 'quote for 20 pcs ...'). "
        "If no quantity is given, do not include a number in the queries. "
        "Never make up or guess a quantity. "
        "Return JSON: {'part_numbers': [...], 'quantity': int or null, 'queries': [...]}."
    )
    user = request_string
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": user}
        ],
        max_tokens=400,
        temperature=0,
        response_format={"type": "json_object"}
    )
    return json.loads(resp.choices[0].message.content)



def scrape_page(url, max_images=5, stream_id=None):
    headers = {
        "User-Agent": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/{random.randint(500,599)}.36 (KHTML, like Gecko) Chrome/11{random.randint(0,99)}.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        if stream_id and STOP_FLAGS.get(stream_id):
            print("[STOP CHECK] Stopped before soup parsing")
            return "", []
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]): tag.extract()
        text = ' '.join(soup.stripped_strings)
        images = []
        for img in soup.find_all("img"):
            if stream_id and STOP_FLAGS.get(stream_id):
                print("[STOP CHECK] Stopped while collecting images")
                return text[:8000], images
            src = img.get("src", "")
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                
                src = urljoin(url, src)
            if src.startswith("http") and src not in images:
                images.append(src)
            if len(images) >= max_images:
                break
        return text[:8000], images
    except Exception as e:
        return f"Failed to scrape {url}: {e}", []

def analyze_with_gpt(part_numbers, quantity, url, text, images, stream_id=None):
    if stream_id and STOP_FLAGS.get(stream_id):
        print("[STOP CHECK] Stopped before GPT call")
        return {'found': False, 'images': []}

    # Format the part_numbers for prompt clarity
    part_number_list = (
        part_numbers if isinstance(part_numbers, list) else [part_numbers]
    )
    part_number_str = ', '.join(str(pn) for pn in part_number_list)

    sys = (
        "You are an expert in aerospace/military part procurement. "
        "ONLY return information about the exact part numbers given below or their *officially documented* equivalents/cross-references "
        "(for example, if a manufacturer's datasheet or a cross-reference chart in the page matches a given number to another, that's acceptable). "
        "Do NOT guess, and do NOT return results for unrelated or only-similar part numbers. "
        "If there is no direct match to the requested part numbers or cross-reference, set 'found' to false. "
        "Given web text and a list of image URLs scraped from the page, decide whether any of the images are worth sending to a user looking for technical information "
        "(e.g., datasheets, diagrams, technical specs, clear product photos). "
        "For each image, review its filename/URL context and (optionally) any nearby short textual description, and only include in your output those that are genuinely helpful. "
        "If diagrams/images were present and useful, briefly describe them in 'context' for the user. "
        "If you find a supplier, quote, or price for the part, return it. "
        "Return your answer as a JSON object with this format:\n"
        "{'found': bool, 'part_number': str, 'equivalent': str or null, 'quantity': int or null, 'price': float or null, 'currency': str or null, 'context': str or null, 'supplier': str or null, 'images': list of filtered image URLs, 'images_description': str or null}.\n"
        "If no relevant info is found, return {'found': false, 'images': []}."
    )
    user = (
        f"Requested part numbers: {part_number_str}\n"
        f"Desired quantity: {quantity if quantity is not None else 'Not specified'}\n"
        f"URL: {url}\n"
        f"Content:\n{text}\n"
        f"Images: {images if images else 'None'}"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            max_tokens=700,
            response_format={"type": "json_object"}
        )
        response = json.loads(resp.choices[0].message.content)
        response["images"] = response.get("images", []) or []

        # Extra backend check: Only accept if part_number matches input or is a cross-ref
        if not response.get("found"):
            return response

        found_number = str(response.get("part_number", "")).upper()
        # Accept if direct match or cross-ref'd (as indicated by LLM with 'equivalent')
        is_direct_match = any(str(pn).upper() == found_number for pn in part_number_list)
        is_cross_ref = (
            response.get("equivalent") 
            and any(str(pn).upper() == str(response["equivalent"]).upper() for pn in part_number_list)
        )
        if not (is_direct_match or is_cross_ref):
            response["found"] = False
            response["reason"] = "No exact or official cross-reference match to requested part numbers."
            response["images"] = []
        return response
    except Exception as e:
        print(f"[STOP CHECK] GPT call error or stopped: {e}")
        return {'found': False, 'images': []}


def stream_auto_part_search(request_string, info_type, stream_id):
    try:
        print(f"[STREAM] Started stream with id {stream_id}")
        yield json.dumps({"status": "Extracting part numbers & queries...", "percent": 2}) + "\n"
        info = extract_info_and_queries(request_string)
        if STOP_FLAGS.get(stream_id):
            print("[STOP CHECK] Stopped after extract_info_and_queries")
            yield json.dumps({"status": "Stopped by user", "stopped": True, "percent": 100, "offers": [], "useful_sites": []}) + "\n"
            return

        part_numbers = info["part_numbers"]
        quantity = info.get("quantity", None)
        queries = info["queries"]

        offers = []
        useful_sites = []
        max_urls_per_query = 3
        jobs = []
        for query in queries:
            if STOP_FLAGS.get(stream_id):
                print("[STOP CHECK] Stopped during job build")
                yield json.dumps({"status": "Stopped by user", "stopped": True, "percent": 100, "offers": offers, "useful_sites": useful_sites}) + "\n"
                return
            urls = duckduckgo_search(query, max_sites=3, stream_id=stream_id)
            if STOP_FLAGS.get(stream_id):
                print("[STOP CHECK] Stopped after search")
                yield json.dumps({"status": "Stopped by user", "stopped": True, "percent": 100, "offers": offers, "useful_sites": useful_sites}) + "\n"
                return
            for url in urls[:max_urls_per_query]:
                jobs.append((query, url))
        total_steps = len(jobs)
        if total_steps == 0:
            yield json.dumps({"status": "No search results found.", "percent": 100, "offers": [], "useful_sites": []}) + "\n"
            return

        for idx, (query, url) in enumerate(jobs):
            if STOP_FLAGS.get(stream_id):
                print(f"[STOP CHECK] Stopped main loop at idx {idx}")
                yield json.dumps({"status": "Stopped by user", "stopped": True, "percent": 100, "offers": offers, "useful_sites": useful_sites}) + "\n"
                return

            progress = int((idx / total_steps) * 98) + 2
            yield json.dumps({"status": f"Scraping: {url}", "percent": progress}) + "\n"
            page_text, images = scrape_page(url, stream_id=stream_id)
            if STOP_FLAGS.get(stream_id):
                print(f"[STOP CHECK] Stopped after scrape {url}")
                yield json.dumps({"status": "Stopped by user", "stopped": True, "percent": 100, "offers": offers, "useful_sites": useful_sites}) + "\n"
                return

            print(f"\n====[ SCRAPED CONTENT: {url} ]====")
            scraped_lines = page_text.split('\n') if '\n' in page_text else page_text.split('. ')
            for line in scraped_lines[:50]:
                print(line)
            print("====[ END SCRAPED CONTENT ]====\n")

            if isinstance(page_text, str) and page_text.startswith("Failed to scrape"):
                yield json.dumps({"status": f"Failed to scrape {url}", "percent": progress}) + "\n"
                continue

            yield json.dumps({"status": f"Analyzing: {url}", "percent": progress}) + "\n"
            gpt_summary = analyze_with_gpt(part_numbers, quantity, url, page_text, images, stream_id=stream_id)
            if STOP_FLAGS.get(stream_id):
                print(f"[STOP CHECK] Stopped after GPT {url}")
                yield json.dumps({"status": "Stopped by user", "stopped": True, "percent": 100, "offers": offers, "useful_sites": useful_sites}) + "\n"
                return

            print(f"\n====[ GPT REVIEW for {url} ]====")
            print(json.dumps(gpt_summary, indent=2))
            print("====[ END GPT REVIEW ]====\n")

            gpt_summary["url"] = url
            if gpt_summary.get("found"):
                offers.append(gpt_summary)
                if url not in useful_sites:
                    useful_sites.append(url)
                yield json.dumps({
                    "offer": gpt_summary,
                    "status": "Found an offer!",
                    "percent": progress
                }) + "\n"
            # Responsive sleep
            if stoppable_sleep(random.uniform(0.5, 1.3), stream_id):
                print(f"[STOP CHECK] Stopped during sleep at idx {idx}")
                yield json.dumps({"status": "Stopped by user", "stopped": True, "percent": 100, "offers": offers, "useful_sites": useful_sites}) + "\n"
                return

        yield json.dumps({
            "offers": offers,
            "useful_sites": useful_sites,
            "status": "Done",
            "percent": 100
        }) + "\n"
    finally:
        print(f"[STREAM] Ended stream with id {stream_id}")
        STOP_FLAGS.pop(stream_id, None)

# ========== FLASK ROUTES ==========

@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json(force=True)
    req_str = data.get("request_string", "")
    info_type = data.get("info_type", None)
    stream_id = data.get("stream_id") or str(uuid.uuid4())
    STOP_FLAGS[stream_id] = False  # Mark as not stopped
    def event_stream():
        yield from stream_auto_part_search(req_str, info_type, stream_id)
    headers = {'X-Accel-Buffering': 'no'}
    return Response(event_stream(), mimetype="text/plain", headers=headers)

@app.route("/api/stop", methods=["POST"])
def api_stop():
    data = request.get_json(force=True)
    stream_id = data.get("stream_id")
    if stream_id and stream_id in STOP_FLAGS:
        STOP_FLAGS[stream_id] = True
        return jsonify({"stopped": True})
    return jsonify({"error": "No active search with that stream_id"}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
