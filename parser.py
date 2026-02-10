from curl_cffi import requests
import json
import csv
import html
import os
import logging
import re
import random
from datetime import datetime
from bs4 import BeautifulSoup, NavigableString

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

REDDIT_URL = "https://api.reddit.com/r/CrackWatch/comments/p9ak4n/crack_watch_games"
OUTPUT_FILE = "denuvo_games.json"
OUTPUT_CSV = "denuvo_games.csv"

def get_proxy_pool():
    """Retrieves a list of proxies from environment variables and Webshare API."""
    proxies = []
    
    # 1. Static List from PROXIES_LIST env var
    static_list = os.environ.get("PROXIES_LIST")
    if static_list:
        proxies.extend([p.strip() for p in static_list.split(",") if p.strip()])
        
    # 2. Webshare API
    webshare_key = os.environ.get("WEBSHARE_API_KEY")
    if webshare_key:
        try:
            logger.info("Fetching Webshare proxies...")
            resp = requests.get(
                "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=9999",
                headers={"Authorization": f"Token {webshare_key}"},
                impersonate="chrome",
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            count = 0
            for p in data.get("results", []):
                if p.get("valid"):
                    # Format: http://username:password@ip:port
                    proxy_str = f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
                    proxies.append(proxy_str)
                    count += 1
            logger.info(f"Fetched {count} proxies from Webshare.")
        except Exception as e:
            logger.error(f"Failed to fetch Webshare proxies: {e}")
            
    # Deduplicate
    return list(set(proxies))

def fetch_reddit_data():
    """Fetches the Reddit thread JSON and extracts the selftext_html using proxies."""
    proxy_pool = get_proxy_pool()
    max_retries = 10
    
    # If no proxies configured, try direct connection once
    if not proxy_pool:
        logger.warning("No proxies configured. Attempting direct connection.")
        try:
            response = requests.get(REDDIT_URL, impersonate="safari18_4_ios")
            response.raise_for_status()
            data = response.json()
            post_data = data[0]['data']['children'][0]['data']
            selftext_html = post_data.get('selftext_html')
            if not selftext_html:
                raise ValueError("selftext_html not found in Reddit response")
            return selftext_html
        except Exception as e:
            logger.error(f"Error fetching Reddit data (direct): {e}")
            raise

    # Try with proxies
    logger.info(f"Starting fetch with {len(proxy_pool)} proxies available. Max retries: {max_retries}")
    
    for attempt in range(max_retries):
        if not proxy_pool:
            raise Exception("No more proxies available to try.")
            
        # Select random proxy and remove it from pool to avoid reuse on failure
        proxy = random.choice(proxy_pool)
        proxy_pool.remove(proxy)
        
        try:
            response = requests.get(
                REDDIT_URL,
                impersonate="safari18_4_ios",
                proxy={"http": proxy, "https": proxy},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            
            # Structure: [ { "data": { "children": [ { "data": { "selftext_html": "..." } } ] } } ]
            post_data = data[0]['data']['children'][0]['data']
            selftext_html = post_data.get('selftext_html')
            
            if not selftext_html:
                raise ValueError("selftext_html not found in Reddit response")
                
            logger.info("Successfully fetched data from Reddit.")
            return selftext_html
            
        except Exception as e:
            # Log generic error, avoid logging proxy credentials
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed. Retrying with different proxy...")
            continue
            
    raise Exception(f"Failed to fetch Reddit data after {max_retries} attempts.")

def normalize_name(name):
    """Normalizes the game name by allowing only alphanumeric characters, and converting to lowercase."""
    return re.sub(r'[^a-zA-Z0-9]', '', name).lower()

def parse_date(date_str):
    """Parses a date string into ISO 8601 UTC format if possible."""
    if not date_str:
        return date_str
        
    try:
        # Try parsing YYYY-MM-DD
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%dT00:00:00Z")
    except ValueError:
        # Return original if parsing fails (e.g. "TBA")
        return date_str

def parse_cracker_list(cell):
    """Parses the 'By' cell into a list of crackers."""
    # Extract text, ignoring marker links like #drmfree
    text_parts = []
    for child in cell.children:
        if isinstance(child, NavigableString):
            text_parts.append(str(child))
        elif child.name == 'a':
            # Skip marker links
            href = child.get('href', '')
            if not href.startswith('#'):
                text_parts.append(child.get_text())
        else:
            text_parts.append(child.get_text())
            
    full_text = "".join(text_parts)
    
    # Split by / or + surrounded by optional whitespace
    parts = re.split(r'\s*[/+]\s*', full_text)
    
    # Clean up parts
    cleaned_parts = [p.strip() for p in parts if p.strip()]
    
    return cleaned_parts

def extract_store_id(url):
    """Extracts the store ID from the store link."""
    if not url:
        return None
        
    # Steam: https://store.steampowered.com/app/1239520 or https://steampowered.com/app/1239520
    steam_match = re.search(r'(?:store\.)?steampowered\.com/(?:agecheck/)?app/(\d+)', url)
    if steam_match:
        return steam_match.group(1)
        
    # Epic: https://store.epicgames.com/en-US/p/prince-of-persia-the-lost-crown
    epic_match = re.search(r'store\.epicgames\.com/.*/p/([^/?]+)', url)
    if epic_match:
        return epic_match.group(1)
        
    # Ubisoft: https://store.ubisoft.com/ie/rabbids-party-of-legends/624effdf50e7e27306220ca7.html
    ubisoft_match = re.search(r'store\.ubisoft\.com/.*/([^/]+)\.html', url)
    if ubisoft_match:
        return ubisoft_match.group(1)
        
    return None

def parse_name_cell(cell):
    """Parses the 'Name' cell to extract name and metadata."""
    metadata = {
        'denuvo_assumption': False,
        'denuvo_assumption_desc': "Confirmed"
    }
    raw_name_parts = []
    
    # Iterate over child nodes to separate text from markers
    for child in cell.children:
        if isinstance(child, NavigableString):
            raw_name_parts.append(str(child))
        elif child.name == 'sup':
            text = child.get_text().strip()
            if '(+)' in text:
                metadata['crack_status'] = 'fully_cracked'
                metadata['crack_status_desc'] = "latest update is cracked with all the DLC's"
            elif '(–)' in text or '(-)' in text:
                metadata['crack_status'] = 'missing_updates'
                metadata['crack_status_desc'] = "all DLC's are cracked, but the latest patch is uncracked"
            elif '(x)' in text:
                metadata['crack_status'] = 'missing_dlc'
                metadata['crack_status_desc'] = "the latest DLC or multiple ones are uncracked"
            elif 'A' in text:
                metadata['denuvo_assumption'] = True
                metadata['denuvo_assumption_desc'] = "Assumption"
        elif child.name == 'a':
            href = child.get('href', '')
            if href == '#uplay':
                metadata['drm_protection'] = 'Uplay/Ubisoft Connect'
            elif href == '#epic':
                metadata['store_exclusive'] = 'Epic Games Store'
            elif href == '#gog':
                metadata['gog_available'] = True
                metadata['gog_desc'] = "Steam version still uses Denuvo, GOG version available"
            elif href == '#drmfree':
                metadata['crack_type'] = 'drm_free_leak'
            else:
                # Regular link inside name? Treat as text if it's not a marker
                if not href.startswith('#'):
                    raw_name_parts.append(child.get_text())

    full_name = "".join(raw_name_parts).strip()
    normalized = normalize_name(full_name)
    
    result = {
        "name": full_name,
        "normalized_name": normalized
    }
    result.update(metadata)
    return result

def parse_denuvo_html(html_content):
    """Parses the HTML content to extract Denuvo game tables."""
    unescaped_html = html.unescape(html_content)
    soup = BeautifulSoup(unescaped_html, 'html.parser')
    
    results = {}
    
    # Add Legend and Notes
    results['legend'] = {
        "(+)": "latest update is cracked with all the DLC's",
        "(-)": "all DLC's are cracked, but the latest patch is uncracked",
        "(x)": "the latest DLC or multiple ones are uncracked",
        "A": "Not confirmed but based on the assumption that the game will use Denuvo",
        "Uplay": "Protected by Uplay/Ubisoft Connect DRM",
        "Epic": "Epic Store exclusive",
        "GOG": "Steam version still uses Denuvo, GOG version available",
        "DRM Free": "Cracked using a DRM free leak (Usually P2P)"
    }
    
    # Extract UWP Note
    uwp_note = "UWP version has no Denuvo. It uses UWP DRM instead. Steam version still has Denuvo."
    # Try to find it in the HTML to be sure, but hardcoding is safer given the requirement
    results['notes'] = [uwp_note]

    potential_headers = soup.find_all('strong')
    
    for header in potential_headers:
        header_text = header.get_text().strip()
        
        # Filter for relevant sections
        if "DENUVO" not in header_text.upper():
            continue
            
        parent_p = header.find_parent('p')
        if not parent_p:
            continue
            
        # Find the next sibling that is a table
        next_element = parent_p.find_next_sibling()
        while next_element and next_element.name != 'table':
            # Stop if we hit another header or end of section (hr)
            if next_element.name in ['h1', 'h2', 'hr', 'p']:
                 if next_element.name == 'p' and next_element.find('strong'):
                     break
            next_element = next_element.find_next_sibling()
            
        if next_element and next_element.name == 'table':
            logger.info(f"Found table for section: {header_text}")
            table_data = parse_table(next_element)
            
            # Clean up section name for JSON key
            key = header_text.lower().replace(' ', '_').replace('**', '')
            results[key] = table_data
            
    return results

def parse_table(table):
    """Parses a single HTML table into a list of dictionaries."""
    rows = []
    headers = []
    
    # Parse headers
    thead = table.find('thead')
    if thead:
        header_cells = thead.find_all('th')
        headers = [cell.get_text().strip() for cell in header_cells]
    else:
        # Fallback if no thead, try first row
        first_row = table.find('tr')
        if first_row:
            header_cells = first_row.find_all(['th', 'td'])
            headers = [cell.get_text().strip() for cell in header_cells]
            
    # Parse body
    tbody = table.find('tbody')
    if not tbody:
        tbody = table # Sometimes rows are direct children
        
    data_rows = tbody.find_all('tr')
    
    for row in data_rows:
        cells = row.find_all('td')
        if not cells:
            continue
            
        # Skip if it's a header row inside tbody (unlikely but possible)
        if len(cells) != len(headers):
            pass
            
        row_data = {}
        for i, cell in enumerate(cells):
            if i < len(headers):
                col_name = headers[i].lower().replace(' ', '_')
                
                if col_name == 'name':
                    name_data = parse_name_cell(cell)
                    row_data.update(name_data)
                elif col_name in ['released', 'cracked', 'release']:
                    cell_text = cell.get_text().strip()
                    row_data[col_name] = parse_date(cell_text)
                elif col_name in ['by', 'bypassed_by']:
                    row_data[col_name] = parse_cracker_list(cell)
                elif col_name == 'playable_on_emulator':
                    cell_text = cell.get_text().strip()
                    if cell_text.lower().startswith('yes'):
                        row_data['playable_on_emulator'] = True
                        # Extract content in parentheses
                        match = re.search(r'\((.*?)\)', cell_text)
                        if match:
                            row_data['emulator_name'] = match.group(1)
                        else:
                            row_data['emulator_name'] = None
                    else:
                        row_data['playable_on_emulator'] = False
                        row_data['emulator_name'] = None
                else:
                    # Extract text
                    cell_text = cell.get_text().strip()
                    
                    # Extract link if present (especially for Store Link)
                    link = cell.find('a')
                    if link and link.get('href'):
                        if "link" in col_name:
                            row_data[col_name] = link.get('href')
                            # Extract store ID
                            store_id = extract_store_id(link.get('href'))
                            if store_id:
                                row_data['store_id'] = store_id
                        else:
                            row_data[col_name] = cell_text
                    else:
                        row_data[col_name] = cell_text
                    
        if row_data:
            rows.append(row_data)
            
    return rows

def save_to_json(data, filepath):
    """Saves the data to a JSON file."""
    # Ensure directory exists
    if os.path.dirname(filepath):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved data to {filepath}")

def save_to_csv(data, filepath):
    """Saves the parsed data to a CSV file."""
    # Ensure directory exists
    if os.path.dirname(filepath):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
    # Collect all rows and determine all possible field names
    all_rows = []
    fieldnames = set()
    
    for section, content in data.items():
        # Skip metadata like 'legend' or 'notes'
        if not isinstance(content, list):
            continue
        if not content: # Empty list
            continue
        if not isinstance(content[0], dict): # List of strings (like notes)
            continue
            
        for row in content:
            csv_row = row.copy()
            csv_row['section'] = section
            
            # Flatten lists (like 'by') into strings for CSV
            for k, v in csv_row.items():
                if isinstance(v, list):
                    csv_row[k] = ", ".join(v)
            
            all_rows.append(csv_row)
            fieldnames.update(csv_row.keys())
            
    if not all_rows:
        logger.warning("No data to save to CSV.")
        return

    # Organize fieldnames: name, section, then the rest sorted
    sorted_fieldnames = ['name', 'section']
    other_fields = sorted(list(fieldnames - {'name', 'section'}))
    sorted_fieldnames.extend(other_fields)
    
    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=sorted_fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
        
    logger.info(f"Saved data to {filepath}")

def main():
    try:
        html_content = fetch_reddit_data()
        parsed_data = parse_denuvo_html(html_content)
        
        if not parsed_data:
            logger.warning("No data parsed! Check the HTML structure or selectors.")
        else:
            logger.info(f"Parsed {len(parsed_data)} sections.")
            
        save_to_json(parsed_data, OUTPUT_FILE)
        save_to_csv(parsed_data, OUTPUT_CSV)
        
    except Exception as e:
        logger.error(f"Script failed: {e}")
        exit(1)

if __name__ == "__main__":
    main()
