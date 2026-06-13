from curl_cffi import requests, Session
import json
import csv
import html
import os
import logging
import re
import random
import base64
from datetime import datetime, timedelta
from bs4 import BeautifulSoup, NavigableString
from dotenv import load_dotenv

# local dev
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Reddit anonymous OAuth credentials (set via environment / GitHub secrets)
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "")
REDDIT_ANON_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_URL = "https://api.reddit.com/r/CrackWatch/comments/p9ak4n/crack_watch_games"
REDDIT_OAUTH_URL = "https://oauth.reddit.com/r/CrackWatch/comments/p9ak4n/.json"

# VR games thread URLs
REDDIT_VR_API_URL = "https://api.reddit.com/r/CrackWatch/comments/fs9xy2/crack_watch_vr_games_and_games_that_require"
REDDIT_VR_OAUTH_URL = "https://oauth.reddit.com/r/CrackWatch/comments/fs9xy2/.json"

OUTPUT_FILE = "denuvo_games.json"
OUTPUT_CSV = "denuvo_games.csv"


class RedditClient:
    """Unified Reddit API client with OAuth, proxy rotation, and retry logic."""

    IMPERSONATE = "safari18_4_ios"
    TIMEOUT = 30
    MAX_RETRIES = 10
    TOKEN_BUFFER_SECONDS = 60

    def __init__(self, client_id: str = "", user_agent: str = ""):
        self.client_id = client_id
        self.user_agent = user_agent
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._proxy_pool: list[str] = []
        self.session = Session(impersonate=self.IMPERSONATE)

    def close(self):
        """Closes the underlying session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def fetch_anon_token(self) -> str:
        """Fetches an anonymous OAuth access token from Reddit.

        Uses the installed_client grant (the correct anonymous flow for
        installed apps): POST to /api/v1/access_token with grant_type=installed_client
        and Basic auth (client_id:).

        Uses a proxy from the pool if one is available to avoid exposing
        the host IP.
        """
        if not self.client_id:
            raise ValueError("REDDIT_CLIENT_ID is required for OAuth token fetch")

        credentials = base64.b64encode(f"{self.client_id}:".encode()).decode()

        headers = {
            "Authorization": f"Basic {credentials}",
            "User-Agent": self.user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {
            "grant_type": "https://oauth.reddit.com/grants/installed_client",
            "device_id": "DO_NOT_TRACK_THIS_DEVICE",
        }

        self._refresh_proxy_pool()
        proxies = self._select_proxy()
        if proxies:
            logger.info("Fetching OAuth token via proxy.")

        response = self.session.post(
            REDDIT_ANON_TOKEN_URL,
            headers=headers,
            data=data,
            proxies=proxies,
            timeout=15
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise ValueError(f"No access_token in response: {token_data}")

        expires_in = token_data.get("expires_in", 0)
        self._access_token = access_token
        self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)
        logger.info(f"Obtained anonymous OAuth token (expires in {expires_in}s)")
        return access_token

    def _ensure_token(self) -> str:
        """Returns a valid access token, fetching a new one if needed."""
        if self._access_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at - timedelta(seconds=self.TOKEN_BUFFER_SECONDS):
                return self._access_token

        return self.fetch_anon_token()

    def _refresh_proxy_pool(self):
        """Fetches and caches the proxy pool from environment / Webshare API."""
        if self._proxy_pool:
            return

        self._proxy_pool = get_proxy_pool()
        if self._proxy_pool:
            logger.info(f"Proxy pool loaded with {len(self._proxy_pool)} proxies.")
        else:
            logger.info("No proxies configured.")

    def _select_proxy(self) -> dict[str, str] | None:
        """Selects a random proxy and removes it from the pool to avoid reuse."""
        if not self._proxy_pool:
            return None

        proxy = random.choice(self._proxy_pool)
        self._proxy_pool.remove(proxy)
        return {"http": proxy, "https": proxy}

    def _build_headers(self) -> dict[str, str]:
        """Builds standardized request headers with OAuth if available."""
        headers = {"User-Agent": self.user_agent}
        if self.client_id:
            token = self._ensure_token()
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _build_request_kwargs(self, headers: dict, proxies: dict[str, str] | None = None) -> dict:
        """Builds standardized request kwargs."""
        kwargs = {
            "headers": headers,
            "timeout": self.TIMEOUT,
        }
        if proxies:
            kwargs["proxies"] = proxies
        return kwargs

    def request(self, url: str, use_proxy: bool = True) -> requests.Response:
        """Performs an HTTP request with retry logic, proxy rotation, and OAuth.

        Args:
            url: The URL to request.
            use_proxy: Whether to attempt proxy rotation (default True).

        Returns:
            The response object.

        Raises:
            Exception: If all attempts fail.
        """
        max_attempts = self.MAX_RETRIES if use_proxy and self._proxy_pool else 1

        for attempt in range(max_attempts):
            try:
                # Refresh proxy pool on first attempt if needed
                if use_proxy and attempt == 0:
                    self._refresh_proxy_pool()

                # Select proxy
                if use_proxy:
                    if not self._proxy_pool:
                        raise RuntimeError(
                            "Proxy pool exhausted"
                        )
                    proxies = self._select_proxy()
                    if proxies is None:
                        raise RuntimeError(
                            "Proxy pool exhausted"
                        )
                else:
                    proxies = None

                # Build headers and kwargs
                headers = self._build_headers()
                kwargs = self._build_request_kwargs(headers, proxies)

                logger.debug(f"Attempt {attempt + 1}/{max_attempts}: GET {url}")
                response = self.session.get(url, **kwargs)
                response.raise_for_status()
                return response

            except Exception as e:
                if attempt == max_attempts - 1:
                    raise
                logger.warning(f"Attempt {attempt + 1}/{max_attempts} failed: {e}. Retrying...")

    def _parse_response(self, response: requests.Response) -> str:
        """Extracts selftext_html from a Reddit API JSON response."""
        data = response.json()
        post_data = data[0]['data']['children'][0]['data']
        selftext_html = post_data.get('selftext_html')
        if not selftext_html:
            raise ValueError("selftext_html not found in Reddit response")
        return selftext_html

    def fetch_reddit_thread(
        self,
        api_url: str = REDDIT_API_URL,
        oauth_url: str = REDDIT_OAUTH_URL,
    ) -> str:
        """Fetches a Reddit thread and extracts selftext_html.

        Uses OAuth + proxies if configured, otherwise falls back to
        unauthenticated direct request.

        Args:
            api_url: Public API URL (used when no OAuth).
            oauth_url: OAuth URL (used when OAuth credentials available).
        """
        use_oauth = bool(self.client_id and self.user_agent)

        # No OAuth — use public API (with proxy if available)
        if not use_oauth:
            self._refresh_proxy_pool()
            if self._proxy_pool:
                logger.info(
                    f"No OAuth credentials — using public API via "
                    f"{len(self._proxy_pool)} proxies."
                )
                use_proxy = True
            else:
                logger.warning(
                    "No OAuth credentials or proxies configured — "
                    "using unauthenticated direct request (may be rate-limited)"
                )
                use_proxy = False

            try:
                response = self.request(api_url, use_proxy=use_proxy)
                return self._parse_response(response)
            except Exception as e:
                if use_proxy:
                    logger.error(
                        "Proxies were configured — not falling back to direct request "
                        "(would expose host IP). Aborting."
                    )
                    raise
                raise

        # OAuth path (with or without proxies)
        logger.info("Attempting connection with anonymous OAuth.")

        try:
            self._refresh_proxy_pool()
            use_proxy = bool(self._proxy_pool)
            if use_proxy:
                logger.info(f"Using {len(self._proxy_pool)} proxies. Max retries: {self.MAX_RETRIES}")
            response = self.request(oauth_url, use_proxy=use_proxy)
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"OAuth request failed: {e}")
            if use_proxy:
                logger.error(
                    "Proxies were configured — not falling back to direct request "
                    "(would expose host IP). Aborting."
                )
                raise
            logger.warning("Falling back to unauthenticated request.")
            return self._fetch_direct(api_url)

    def _fetch_direct(self, url: str) -> str:
        """Performs a direct (non-proxy, non-OAuth) request."""
        response = self.session.get(
            url,
            timeout=self.TIMEOUT
        )
        response.raise_for_status()
        return self._parse_response(response)

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


def fetch_reddit_data(client: RedditClient) -> str:
    """Fetches the main Denuvo games Reddit thread and extracts selftext_html.

    Uses the provided RedditClient instance.
    """
    return client.fetch_reddit_thread()


def fetch_vr_games_data(client: RedditClient) -> str:
    """Fetches the VR games Reddit thread and extracts selftext_html.

    Reuses the provided RedditClient with VR-specific thread URLs.
    """
    return client.fetch_reddit_thread(
        api_url=REDDIT_VR_API_URL,
        oauth_url=REDDIT_VR_OAUTH_URL,
    )


def parse_vr_games_html(html_content) -> list[dict]:
    """Parses the VR games HTML content, extracting only VR GAMES tables.

    Returns a list of game dicts with fields:
    name, normalized_name, released, cracked, by
    """
    unescaped_html = html.unescape(html_content)
    soup = BeautifulSoup(unescaped_html, 'html.parser')

    vr_games = []

    # Find all <p> tags containing "VR GAMES" in <strong>
    vr_headers = []
    for p in soup.find_all('p'):
        strong = p.find('strong')
        if strong and 'VR GAMES' in strong.get_text().strip():
            vr_headers.append(p)

    if not vr_headers:
        logger.warning("No VR GAMES headers found in HTML.")
        return vr_games

    # only first "VR GAMES" table (under the <h1>Denuvo</h1> section) contains vr protected denuvo games.
    header = vr_headers[0]

    # Find the next sibling table after the header <p>
    next_element = header.find_next_sibling()
    while next_element and next_element.name != 'table':
        if next_element.name in ['h1', 'h2', 'hr', 'p']:
            if next_element.name == 'p' and next_element.find('strong'):
                break
        next_element = next_element.find_next_sibling()

    if next_element and next_element.name == 'table':
        logger.info("Found VR GAMES table (Denuvo)")
        table = next_element
        tbody = table.find('tbody')
        if not tbody:
            tbody = table

        for row in tbody.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) < 4:
                continue

            name_text = cells[0].get_text().strip()
            released_text = cells[1].get_text().strip()
            cracked_text = cells[2].get_text().strip()
            by_text = cells[3].get_text().strip()

            if not name_text:
                continue

            game = {
                "name": name_text,
                "normalized_name": normalize_name(name_text),
                "released": parse_date(released_text) if released_text else None,
                "cracked": parse_date(cracked_text) if cracked_text and cracked_text.lower() != 'never' else None,
                "by": by_text if by_text else None,
            }
            vr_games.append(game)

    logger.info(f"Parsed {len(vr_games)} VR games.")
    return vr_games


def merge_vr_into_sections(parsed_data: dict, vr_games: list[dict]) -> dict:
    """Routes each VR game into the appropriate section of parsed_data.

    Routing rules:
    - "By" contains "Hypervisor workaround" → bypassed_denuvo_games
    - "Cracked" has date + "By" has cracker + released >= 2021 → cracked_denuvo_games_(2021-present)
    - "Cracked" has date + "By" has cracker + released < 2021 → older_cracked_denuvo_games_(2014-2020)
    - "Cracked" is empty/None or "Never" + "By" is empty → uncracked_denuvo_games
    """
    for game in vr_games:
        name = game["name"]
        normalized_name = game["normalized_name"]
        released = game["released"]
        cracked = game["cracked"]
        by = game["by"]

        # VR games have simpler schemas: no store_link, no store_id, no DRM markers
        base_entry = {
            "name": name,
            "normalized_name": normalized_name,
            "hypervisor_available": False,
            "denuvo_assumption": False,
            "denuvo_assumption_desc": "Confirmed"
        }

        # Determine release year for section routing
        release_year = None
        if released:
            try:
                release_year = datetime.strptime(released, "%Y-%m-%dT00:00:00Z").year
            except (ValueError, TypeError):
                release_year = None

        # Classification logic
        is_hypervisor = by and "hypervisor" in by.lower()
        is_cracked = cracked is not None
        has_cracker = by is not None and not is_hypervisor

        if is_hypervisor:
            # Hypervisor workaround → bypassed section
            entry = base_entry.copy()
            entry["hypervisor_available"] = True
            entry["released"] = released
            entry["bypassed_by"] = [by] if by else []

            section = "bypassed_denuvo_games"
            parsed_data.setdefault(section, []).append(entry)
            logger.info(f"  VR game '{name}' → {section} (hypervisor)")

        elif is_cracked and has_cracker:
            # Cracked with known cracker
            entry = base_entry.copy()
            entry["crack_status"] = "fully_cracked"
            entry["crack_status_desc"] = "latest update is cracked with all the DLC's"
            entry["released"] = released
            entry["cracked"] = cracked
            entry["cracked_by"] = [
                p.strip() for p in re.split(r'\s*[/+]\s*', by) if p.strip()
            ]

            if release_year and release_year >= 2021:
                section = "cracked_denuvo_games_(2021-present)"
            else:
                section = "older_cracked_denuvo_games_(2014-2020)"

            parsed_data.setdefault(section, []).append(entry)
            logger.info(f"  VR game '{name}' → {section} (cracked)")

        else:
            # Uncracked (no crack date, no cracker, or "Never")
            entry = base_entry.copy()
            entry["released"] = released

            section = "uncracked_denuvo_games"
            parsed_data.setdefault(section, []).append(entry)
            logger.info(f"  VR game '{name}' → {section} (uncracked)")

    return parsed_data


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
    
    # Check for hypervisor tag and extract it
    hypervisor_available = False
    hypervisor_match = re.search(r'\s*\(Hypervisor also available\)\s*$', full_name)
    if hypervisor_match:
        hypervisor_available = True
        full_name = full_name[:hypervisor_match.start()].strip()
    
    normalized = normalize_name(full_name)
    
    result = {
        "name": full_name,
        "normalized_name": normalized,
        "hypervisor_available": hypervisor_available
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
        "DRM Free": "Cracked using a DRM free leak (Usually P2P)",
        "Hypervisor": "Game also has a hypervisor bypass available by DenuvOwO"
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
                elif col_name == 'by':
                    row_data['cracked_by'] = parse_cracker_list(cell)
                elif col_name == 'bypassed_by':
                    bypassed_list = parse_cracker_list(cell)
                    row_data[col_name] = bypassed_list
                    # Check for hypervisor in bypassed_by entries
                    if any('hypervisor' in entry.lower() for entry in bypassed_list):
                        row_data['hypervisor_available'] = True
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
    client = RedditClient(
        client_id=REDDIT_CLIENT_ID,
        user_agent=REDDIT_USER_AGENT,
    )
    try:
        # Fetch and parse main Denuvo games data
        html_content = fetch_reddit_data(client)
        parsed_data = parse_denuvo_html(html_content)

        if not parsed_data:
            logger.warning("No data parsed! Check the HTML structure or selectors.")
        else:
            logger.info(f"Parsed {len(parsed_data)} sections.")

        # Fetch and parse VR games data
        try:
            vr_html = fetch_vr_games_data(client)
            vr_games = parse_vr_games_html(vr_html)
            if vr_games:
                logger.info(f"Merging {len(vr_games)} VR games into sections...")
                parsed_data = merge_vr_into_sections(parsed_data, vr_games)
        except Exception as e:
            logger.error(f"Failed to fetch or parse VR games data: {e}")

        save_to_json(parsed_data, OUTPUT_FILE)
        save_to_csv(parsed_data, OUTPUT_CSV)

    except Exception as e:
        logger.error(f"Script failed: {e}")
        exit(1)
    finally:
        client.close()

if __name__ == "__main__":
    main()
