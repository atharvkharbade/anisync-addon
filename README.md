# 🌟 AniSync - MyAnimeList & AniList Tracker for Stremio

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg?logo=python&logoColor=white)](https://python.org)
[![Quart Version](https://img.shields.io/badge/quart-0.20.0+-00b4d8.svg)](https://pgjones.gitlab.io/quart/)
[![Docker Support](https://img.shields.io/badge/docker-ready-2496ed.svg?logo=docker&logoColor=white)](https://www.docker.com)
[![Stremio Addon](https://img.shields.io/badge/stremio-addon-8a2be2.svg)](https://stremio.com)

**AniSync** is a lightweight companion addon for Stremio. It automatically synchronizes your anime progress to **MyAnimeList** and **AniList** in real-time as you stream, and exposes your watchlists directly inside Stremio as dynamic catalogs.

---

## ✨ Core Features

### 📺 Watchlist Airing Tags (`[New]`)
AniSync checks your watchlists against AniList schedules and places a clear `[New]` tag in front of currently airing anime titles whenever a new episode has aired that you haven't watched yet.
![Airing episode tag indicator](docs/images/watchlist_tags.png)

### 🗂️ Smart Three-Tier Watchlist Sorting
To make your "Currently Watching" watchlist catalog as useful as possible, AniSync automatically sorts and groups your active rows into three distinct tiers:
1. **Unwatched Airing Shows (Top)**: Airing series with newly released, unwatched episodes float to the absolute top of the row with the `[New]` tag so they are instantly accessible.
2. **Completed Series (Middle)**: Finished series that you are currently watching are grouped in the middle, sorted by your last watch update.
3. **Caught-Up Airing Shows (Bottom)**: Shows where you are fully caught up are pushed to the end of the row and sorted chronologically by when their next episode is scheduled to air. This serves as a countdown schedule, putting the soonest-airing shows first.


### 🚫 Episode Filler Indicators (`[Filler]`)
Using the Jikan (MAL) API, AniSync fetches episode lists and automatically prepends a `[Filler]` tag directly to the episode titles in Stremio's player detail overlay, letting you know exactly which episodes are safe to skip.
![Inline episode filler tag details](docs/images/episode_filler_tags.png)

* **Dual Account Syncing**: Connect both MyAnimeList and AniList simultaneously, keeping both watchlists updated in real-time as you play.
* **Draggable Watchlist Catalogs**: Browse your "Currently Watching", "Plan to Watch", "Completed", and other watchlist statuses as rows inside Stremio. You can toggle their visibility and drag-and-drop to reorder them on your dashboard.
* **Unified Kitsu Bridge**: Automatically maps MAL and AniList watchlists back to standard Kitsu IDs, ensuring stream providers (like Torrentio) find and serve playback links.
* **Zero-Lag Background Tracking**: Syncs progress asynchronously by listening to Stremio subtitle requests, meaning your player buffering/playback is never affected.
* **Fast Caching Engine**: Mappings and Jikan filler checks are cached in a local MongoDB instance using bulk page requests, reducing external API calls and keeping catalog loads under 100ms.
* **Web Configuration Dashboard**: An elegant settings page to link your accounts, toggle features, reorder catalogs, and monitor server diagnostics in real-time.

### 🖥️ Web Configuration Dashboard
AniSync includes an elegant, responsive web configuration panel to manage your connections, toggle features, and customize catalog arrangements in real-time:

#### 🔐 Sign-in & Authentication
Connect your MyAnimeList and AniList accounts securely using official OAuth2 sign-in prompts.
![Web Configuration Sign-in](docs/images/dashboard_login.png)

#### 🔗 Account Integrations & Account Management
Manage active connections dynamically with simple disconnect and tracking toggles.
![Web Configuration Main Settings](docs/images/dashboard_main.png)

#### ⚙️ Custom Tracking Preferences
Customize unlisted anime auto-syncing, watchlist sorting priority, and episode filler tags.
![Web Configuration Preferences](docs/images/dashboard_preferences_1.png)

#### 🗂️ Draggable Catalog Ordering & One-Click Install
Manage your Stremio rows, drag-and-drop to reorder your catalogs exactly how they should display in Stremio, and copy the manifest URL or click "Direct Install" to install the addon instantly.
![Web Configuration Catalog Reordering](docs/images/dashboard_preferences_2.png)

---

## ⚠️ Troubleshooting: Metadata Override Conflict (AnimeKitsu Addon)

If you have the **AnimeKitsu** addon installed, it might override AniSync's metadata during playback, which causes our `[Filler]` tags and custom titles to disappear in Stremio's player menu. 

Stremio resolves metadata conflicts by prioritizing whichever addon is **higher in your Stremio installed addons list**. There are two ways to fix this:

### Option A: Reorder your Addons (Highly Recommended)
1. Log in to the community-made **[Stremio Addon Manager](https://addon-manager.ad.sc.strem.io/)** using your Stremio credentials.
2. Drag **AniSync** above **AnimeKitsu** in the list to reorder them.
3. Alternatively, simply uninstall both addons, then **install AniSync first** before reinstalling AnimeKitsu. Stremio will now prioritize our metadata and display the custom tags during playback.

### Option B: Dedicated Sync-Only Mode
If you prefer to let AnimeKitsu handle all of your catalogs and search, you can turn off `"Enable Watchlist Catalogs"` and `"Enable Catalog Search"` in your AniSync dashboard. This runs AniSync silently in the background solely to synchronize your watch progress, leaving Stremio's interface clean and conflict-free.

---

## 🛠️ System Architecture

```mermaid
graph TD
    Stremio[Stremio Client] -->|1. Subtitle request| SubtitleEP[Subtitles Route]
    SubtitleEP -->|2. Resolve ID| IDResolver[ID Resolver]
    IDResolver -->|A. Query Cache| MongoDB[(MongoDB id_cache)]
    IDResolver -->|B. Fallback APIs| ARM[ARM API] & AniZip[ani.zip API] & Fribb[Fribb JSON]
    SubtitleEP -->|3. Sync Progress| SyncService[Sync Services]
    SyncService -->|A. Sync MAL| MAL[MyAnimeList API]
    SyncService -->|B. Sync AniList| AniList[AniList GraphQL]
    
    WebUI[Quart Web Config UI] -->|OAuth Flow| Auth[Auth Routes]
    Auth -->|Store user tokens| MongoUsers[(MongoDB users)]
```

---

## 🚀 Getting Started

### Prerequisites

- [Docker & Docker Compose](https://docs.docker.com/get-docker/) installed.
- Developer accounts for:
  - **MyAnimeList** API Client (Create at [MAL API Config](https://myanimelist.net/apiconfig))
  - **AniList** API Client (Create at [AniList Developer Settings](https://anilist.co/settings/developer))

### 1. Environment Configuration

Clone this repository and create a `.env` file in the root based on `.env.example`:

```bash
cp .env.example .env
```

Fill in the necessary values:

```env
# App Settings
SECRET_KEY=generate-a-long-random-secret-string
FLASK_DEBUG=0
FLASK_RUN_HOST=yourdomain.com  # Public hostname (no protocol)

# MongoDB
MONGO_URI=mongodb://mongo:27017
MONGO_DB=anisync

# MyAnimeList OAuth Configuration
# Set the Redirect URI in MAL panel to: https://yourdomain.com/callback
MAL_CLIENT_ID=your_mal_client_id
MAL_CLIENT_SECRET=your_mal_client_secret

# AniList OAuth Configuration
# Set the Redirect URI in AniList panel to: https://yourdomain.com/anilist-callback
ANILIST_CLIENT_ID=your_anilist_client_id
```

### 2. Spinning Up Services with Docker

AniSync is ready for production out of the box with Docker Compose. Run:

```bash
docker-compose up -d --build
```

This starts:
- **Quart Web Application** on port `5000` (bridged securely to your network manager).
- **MongoDB 7** database daemon with automatic data volumes and health checks.

---

## 🧭 Setup in Stremio

1. Navigate to your deployed instance (e.g. `https://yourdomain.com`).
2. Log in using **MyAnimeList** and/or **AniList** OAuth buttons.
3. Save your preferred configurations (e.g. enabling background sync, auto-adding unlisted series).
4. Copy the generated **Manifest URL** from the dashboard.
5. In the Stremio App:
   - Go to the **Addons** tab.
   - Paste the Manifest URL into the search bar at the bottom left.
   - Click **Install** and approve.

---

## 🧪 Development & Quality Control

This project uses `uv` for dependency management. To set up local quality checks:

### Style Linting
Verify code formatting and styling standards using `ruff`:

```bash
pip install uv
uv pip install -r pyproject.toml ruff
ruff check .
```

### Code Formatting
To format imports and files automatically:

```bash
ruff format .
```

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.
