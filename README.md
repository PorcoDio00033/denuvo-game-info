# Denuvo Games Info

Python-based tool that automatically tracks and provides up-to-date information about Denuvo-protected games in machine-readable formats. It fetches data from [EssenseOfMagic thread](https://www.reddit.com/r/CrackWatch/comments/p9ak4n/crack_watch_games) on [r/CrackWatch](https://www.reddit.com/r/CrackWatch/), parses it, and exports it into [JSON](https://raw.githubusercontent.com/PorcoDio00033/denuvo-game-info/refs/heads/main/denuvo_games.json) and [CSV](https://raw.githubusercontent.com/PorcoDio00033/denuvo-game-info/refs/heads/main/denuvo_games.csv).

Huge thanks to [u/EssenseOfMagic](https://www.reddit.com/user/EssenseOfMagic/) for keeping a nice and updated list.

## Features

*   **Automated Data Fetching:** Retrieves the latest Denuvo game status directly from the r/CrackWatch subreddit API.
*   **Intelligent Parsing:** Extracts detailed information including game names, release dates, crack status, cracker groups, and store links.
*   **Data Normalization:** Standardizes game names and dates for consistency.
*   **Multi-Format Export:** Saves data to both `denuvo_games.json` (structured data) and `denuvo_games.csv` (tabular data).
*   **Detailed Metadata:** Captures nuances like "Uncracked," "Cracked," "Bypassed," "Online Only," and specific crack details (e.g., missing DLCs, specific updates).
*   **Automated Updates:** Includes a GitHub Actions workflow to run the parser daily and commit changes automatically.

## Prerequisites

*   Python 3.12 or higher
*   `pip` (Python package installer)

## Configuration

To avoid Reddit API rate limits (403 errors), you can configure proxies using GitHub Secrets.

### Repository Secrets

*   **`PROXIES_LIST`**: A comma-separated list of proxies in the format `protocol://user:pass@host:port`.
*   **`WEBSHARE_API_KEY`**: Your Webshare.io API key. If provided, the script will fetch the latest proxies from Webshare automatically.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/denuvo-games-info.git
    cd denuvo-games-info
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Usage

To run the parser manually and update the data files:

```bash
python parser.py
```

The script will:
1.  Fetch the latest data from Reddit.
2.  Parse the HTML content.
3.  Generate `denuvo_games.json` and `denuvo_games.csv` in the project root.
4.  Log the progress and any errors to the console.

## Data Structure

### JSON Output (`denuvo_games.json`)
The JSON file is organized into several categories:
*   `legend`: Definitions of symbols used in the data (e.g., `(+)`, `(-)`).
*   `notes`: General notes about the data (e.g., UWP version details).
*   `uncracked_denuvo_games`: List of games that are currently uncracked.
*   `cracked_denuvo_games`: List of games that have been cracked.
*   `bypassed_denuvo_games`: Games with bypass methods available.
*   `upcoming_denuvo_games`: Confirmed or assumed upcoming titles with Denuvo.
*   `free_to_play_games_that_use_denuvo`: F2P titles using Denuvo.
*   `uncracked_denuvo_games_that_are_online_only`: Online-only games that remain uncracked.

**Example Game Object:**
```json
{
  "name": "Example Game",
  "normalized_name": "examplegame",
  "denuvo_assumption": false,
  "denuvo_assumption_desc": "Confirmed",
  "released": "2023-01-01T00:00:00Z",
  "store_link": "https://store.steampowered.com/app/123456",
  "store_id": "123456",
  "playable_on_emulator": false,
  "emulator_name": null
}
```

### CSV Output (`denuvo_games.csv`)
The CSV file flattens the data into a tabular format, including a `section` column to indicate which category the game belongs to. Lists (like "By" for crackers) are comma-separated strings.

## Automation

This project uses **GitHub Actions** to keep the data fresh. The workflow defined in `.github/workflows/update_games.yml`:
*   Runs automatically every day at **00:00 UTC**.
*   Can be triggered manually via the "Run workflow" button in the Actions tab.
*   Commits and pushes any changes to `denuvo_games.json` and `denuvo_games.csv` back to the repository.

## Disclaimer

This tool is for informational and educational purposes only. The data is sourced from public Reddit threads and may not always be 100% accurate or real-time.
