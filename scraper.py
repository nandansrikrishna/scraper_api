from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, quote
import time
import re
import string
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from supabase import create_client, Client
import os
import json

# Initialize FastAPI app
app = FastAPI()

# Supabase setup
url = os.environ.get('SUPABASE_URL')
service_role_key = os.environ.get('SUPABASE_KEY')
supabase: Client = create_client(url, service_role_key)

# Pydantic model for request body
class ScrapeRequest(BaseModel):
    url: str
    community_id: int

@app.post("/api/scrape")
async def scrape(request: ScrapeRequest):
    base_url = request.url
    community_id = request.community_id

    try:
        raw_corpus = crawl_website(base_url)

        prompt = "Strip all unnecessary text that doesnt give useful information for this apartment property. Return only plain text. Simply remove unnecessary lines. Make sure to include all relevant detail from the initial text:\n"
        output = refine_4o(prompt + raw_corpus).text

        refined_text = json.loads(output)["response"]

        dic = {"corpus": refined_text}

        response = update_ai_column(community_id, dic)
        return {"message": "Success", "response": response}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def is_valid_url(url, base_url):
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()

    is_valid_extension = path.endswith('/') or path.endswith('.html')
    exclusion_keywords = ['privacy', 'policy', 'terms', 'service', 'accessibility', 'cookies', 'legal']
    contains_exclusion_keyword = any(keyword in path for keyword in exclusion_keywords)

    return (
        url.startswith(base_url) and
        not parsed_url.fragment and
        is_valid_extension and
        not contains_exclusion_keyword
    )

def is_standard_url(src):
    return src.startswith('http://') or src.startswith('https://')

def preprocess_text(text):
    text = text.lower()
    text = re.sub(f"[{re.escape(string.punctuation)}]", "", text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text

def crawl_website(base_url):
    visited_urls = set()
    to_visit = [base_url]
    collected_img_sources = set()
    tags_content = {'header': None, 'nav': None, 'footer': None}
    page_texts = []

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument("user-agent=insomnia/9.3.3")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    while to_visit:
        current_url = to_visit.pop(0)
        if current_url in visited_urls:
            continue

        try:
            print(f"Crawling: {current_url}")
            driver.get(current_url)
            time.sleep(2)
            
            page_source = driver.page_source
            visited_urls.add(current_url)

            soup = BeautifulSoup(page_source, 'html.parser')

            for link in soup.find_all('a', href=True):
                new_url = urljoin(current_url, link['href'])
                if is_valid_url(new_url, base_url) and new_url not in visited_urls:
                    to_visit.append(new_url)

            tags_to_save = ['header', 'nav', 'footer']
            for tag_name in tags_to_save:
                tag = soup.find(tag_name)
                if tag and tags_content[tag_name] is None:
                    tags_content[tag_name] = preprocess_text(tag.get_text(separator='\n').strip())
                if tag:
                    tag.decompose()

            text_content = re.sub(r'\n+', '\n', soup.get_text(separator='\n').strip())
            preprocessed_text = preprocess_text(text_content)
            page_texts.append(preprocessed_text)

        except Exception as e:
            print(f"Error crawling {current_url}: {e}")

    driver.quit()

    combined_corpus = '\n'.join([tags_content[tag] for tag in tags_to_save if tags_content[tag]] + page_texts)
    
    seen_lines = set()
    final_corpus_lines = []
    for line in combined_corpus.splitlines(keepends=True):
        if line not in seen_lines:
            final_corpus_lines.append(line)
            seen_lines.add(line)
    
    final_corpus = ''.join(final_corpus_lines)
    
    return final_corpus

def refine_4o(prompt):
    url = "https://tour.video/api/ai/completion"
    data = {"prompt": prompt}
    return requests.post(url, json=data)

def update_ai_column(community_id, new_value):
    response = supabase.table("Community").update({"ai": new_value}).eq("id", community_id).execute()
    return response
