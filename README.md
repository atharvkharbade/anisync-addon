<p align="center">
  <img src="docs/images/logo.png" width="120" alt="AniSync Logo" />
</p>

# AniSync - MyAnimeList & AniList Tracker for Stremio

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg?logo=python&logoColor=white)](https://python.org)
[![Quart Version](https://img.shields.io/badge/quart-0.20.0+-00b4d8.svg)](https://pgjones.gitlab.io/quart/)
[![Docker Support](https://img.shields.io/badge/docker-ready-2496ed.svg?logo=docker&logoColor=white)](https://www.docker.com)
[![Stremio Addon](https://img.shields.io/badge/stremio-addon-8a2be2.svg)](https://stremio.com)

**AniSync** automatically synchronizes your anime progress with MyAnimeList and AniList in real-time as you stream, exposing your watchlists as dynamic, beautifully sorted catalog rows directly inside Stremio.

---

## Core Features

### 📺 Poster Badges & Airing Indicators
Never miss a release. Whenever a new episode drops for a show on your watchlist, AniSync overlays a clean `NEW EPISODE` banner directly on the poster in Stremio. If you connect both trackers, it overlays both MyAnimeList and AniList logos side-by-side on the banner.

![Combined Tracker Poster Badges](docs/images/stremio_poster_badges.png)

### 🗂️ Combined Watchlist Catalogs
Why choose between MyAnimeList and AniList? Connect both and AniSync merges them into single, clean, deduplicated catalogs in Stremio.
* **Auto-Merge Progress**: Progress is kept in sync across both trackers, automatically picking whichever progress is higher.
* **AniList Repeating List Support**: Re-watching a series? AniList's `REPEATING` items are automatically merged into your active Watching catalog.

![Stremio Combined Catalogs](docs/images/stremio_combined_watchlists.png)

### 🤖 Personalized Anime Recommendations
Get custom anime recommendation based on your watch history and tastes. Out of the box, AniSync generates tailored recommendations for you.
* **Optional Gemini AI Enhancement**: Supercharge these recommendations by adding a Gemini API key in the settings, allowing Gemini to personalize the rows and generate custom themed lists.

![Stremio Recommendations](docs/images/stremio_gemini_recs.png)

### 🗂️ Smart Three-Tier Watchlist Sorting
To make your "Currently Watching" watchlist catalog as useful as possible, AniSync automatically sorts and groups your active rows into three distinct tiers:
1. **Unwatched Airing Shows (Top)**: Airing series with newly released, unwatched episodes float to the absolute top of the row with the `NEW EPISODE` tag so they are instantly accessible.
2. **Completed Series (Middle)**: Finished series that you are currently watching are grouped in the middle, sorted by your last watch update.
3. **Caught-Up Airing Shows (Bottom)**: Shows where you are fully caught up are pushed to the end of the row and sorted chronologically by when their next episode is scheduled to air. This serves as a countdown schedule, putting the soonest-airing shows first.

### 🚫 Episode Filler Indicators (`[Filler]`)
Using the Jikan API, AniSync fetches episode lists and automatically prepends a `[Filler]` tag directly to the episode titles in Stremio's player detail overlay, letting you know exactly which episodes are safe to skip.

![Inline episode filler tag details](docs/images/stremio_filler_indicators.png)

---

## 🖥️ Web Configuration Dashboard

AniSync includes a responsive web configuration panel to manage your connections, toggle features, and customize catalog arrangements in real-time:

### 🔐 Secure Sign-in & Authentication
Connect your MyAnimeList and AniList accounts securely using official OAuth2 sign-in prompts.

![Web Configuration Sign-in](docs/images/dashboard_login.png)
![Web Configuration Main Settings](docs/images/dashboard_preferences_accounts.png)

### ⚙️ Combined Watchlist Settings
Enable combined catalogs across MyAnimeList and AniList with a single toggle. A built-in guard disables the switch and displays a warning notice if you haven't linked both accounts yet.

![Combined Watchlist Warning](docs/images/dashboard_preferences_warning.png)
![Combined Watchlist Preferences Enabled](docs/images/dashboard_preferences_combined.png)

### 🤖 Recommendations & Optional Gemini Settings
Configure your recommendations settings and optionally link a Gemini API key to personalize and theme the recommendations.

![Gemini AI Settings](docs/images/dashboard_preferences_recs.png)

### 🗂️ Custom Preferences & Sorting
Fine-tune sorting options, search toggles, and filler tag preferences. Drag-and-drop to reorder your catalogs exactly how they should display in Stremio, and save your setup in real-time.

![Sorting Options](docs/images/dashboard_preferences_sorting.png)
![Filler and Search Preferences](docs/images/dashboard_preferences_filler.png)
![Catalog Reordering](docs/images/dashboard_preferences_save.png)

### 📥 One-Click Install
Copy the manifest URL or click "Direct Install" to install the addon instantly in Stremio.

![One-Click Install](docs/images/dashboard_preferences_install.png)

---

## ⚠️ Troubleshooting: Metadata Override Conflict (AnimeKitsu Addon)

If you have the **AnimeKitsu** addon installed, it might override AniSync's metadata during playback, which causes our `[Filler]` tags and custom titles to disappear in Stremio's player menu. 

Stremio resolves metadata conflicts by prioritizing whichever addon is **higher in your Stremio installed addons list**. There are two ways to fix this:

### Option A: Reorder your Addons (Highly Recommended)
1. Log in to the community-made **[Cinebye](https://cinebye.elfhosted.com/)** using your Stremio credentials.
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
    IDResolver -->|A. Query Cache| MongoDB[(MongoDB: Local / Atlas)]
    IDResolver -->|B. Resolve Fallbacks| ConnectionPool[Shared HTTP Connection Pool]
    
    ConnectionPool -->|Optional Proxy Tunnel| Proxy[Proxy Server / Gluetun]
    
    Proxy --> ARM[ARM API] & AniZip[ani.zip API] & Fribb[Fribb JSON] & Kitsu[Kitsu API] & Jikan[Jikan MAL API]
    ConnectionPool -->|Direct Connection| ARM & AniZip & Fribb & Kitsu & Jikan

    SubtitleEP -->|3. Sync Progress| SyncService[Sync Services]
    SyncService --> ConnectionPool
    
    Proxy --> MAL[MyAnimeList API] & AniList[AniList GraphQL]
    ConnectionPool -->|Direct Connection| MAL & AniList
    
    WebUI[Quart Web Config UI] -->|OAuth Flow| Auth[Auth Routes]
    Auth -->|Store user credentials| MongoDB
```

---

## 🚀 Getting Started

### Prerequisites

#### Global Requirements
- Developer accounts for external integrations:
  - **MyAnimeList** API Client (Create at [MAL API Config](https://myanimelist.net/apiconfig))
  - **AniList** API Client (Create at [AniList Developer Settings](https://anilist.co/settings/developer))

#### Deployment-Specific Requirements
- **Option A / B (Docker hosting)**: [Docker & Docker Compose](https://docs.docker.com/get-docker/) installed on your VPS or device.
- **Option C (Vercel serverless hosting)**: A free cloud database cluster set up on **[MongoDB Atlas](https://www.mongodb.com/products/platform/atlas-database)**.

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

# Proxy Support (Optional)
# Route all external API calls through a proxy (HTTP, HTTPS, SOCKS5).
# Perfect for bypassing rate limits at scale on a single server IP.
PROXY_URL=http://username:password@proxyhost.com:8080

# MyAnimeList OAuth Configuration
# Set the Redirect URI in MAL panel to: https://yourdomain.com/callback
MAL_CLIENT_ID=your_mal_client_id
MAL_CLIENT_SECRET=your_mal_client_secret

# AniList OAuth Configuration
# Set the Redirect URI in AniList panel to: https://yourdomain.com/anilist-callback
ANILIST_CLIENT_ID=your_anilist_client_id
```

### 2. Spinning Up Services with Docker

You can spin up AniSync either by pulling our pre-built Docker image from the GitHub Container Registry (GHCR) or by building the source code locally.

#### Option A: Pull Pre-built Image (Quickest & Recommended)

No need to clone the full repository or build the image locally. Simply create a new folder, create a `.env` file (configured as shown above) along with the `docker-compose.yml` file content below, and run:

```bash
docker compose up -d
```

##### Pre-built `docker-compose.yml`

```yaml
services:
  app:
    image: ghcr.io/atharvkharbade/anisync-addon:latest
    container_name: anisync
    mem_limit: 1g
    memswap_limit: 2g
    env_file:
      - .env
    environment:
      - MONGO_URI=mongodb://mongo:27017
    depends_on:
      mongo:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    networks:
      - web-network
      - internal
    restart: unless-stopped

  mongo:
    image: mongo:7
    container_name: anisync-mongo
    mem_limit: 1g
    memswap_limit: 2g
    volumes:
      - mongo_data:/data/db
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - internal
    restart: unless-stopped

volumes:
  mongo_data:

networks:
  web-network:
    external: true
  internal:
    driver: bridge
```

#### Option B: Build from Source (Developers)

If you have cloned the repository and want to build the container from local source code:

1. Build and start the services:
   ```bash
   docker compose up -d --build
   ```
2. This starts:
   - **Quart Web Application** on port `5000` (bridged securely to your network manager).
   - **MongoDB 7** database daemon with automatic data volumes and health checks.

### 3. Hosting on Vercel (Serverless / One-Click)

If you prefer serverless hosting over a private server or Docker container, you can deploy AniSync to **Vercel**:

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2Fatharvkharbade%2Fanisync-addon&env=SECRET_KEY,FLASK_RUN_HOST,MONGO_URI,MONGO_DB,MAL_CLIENT_ID,MAL_CLIENT_SECRET,ANILIST_CLIENT_ID,PROXY_URL&project-name=anisync&repository-name=anisync)

#### Prerequisites for Vercel

Because Vercel is a serverless platform, it cannot host database engines. You will need a cloud-hosted MongoDB database:
1. Create a free database cluster on **[MongoDB Atlas](https://www.mongodb.com/products/platform/atlas-database)**.
2. In the MongoDB Atlas dashboard, click **Connect** -> **Drivers** and copy your Python/standard connection string.
3. Replace `<password>` in the connection string with your actual database user password. This is your `MONGO_URI` connection key.

#### Environment Setup on Vercel

When clicking the Deploy button, Vercel will ask you to supply the following environment variables:
* `SECRET_KEY`: A long, secure random secret string.
* `FLASK_RUN_HOST`: The actual domain Vercel assigns to your project (e.g., `my-anisync-addon.vercel.app` - do not include `https://`).
* `MONGO_URI`: Your MongoDB Atlas connection string.
* `MONGO_DB`: `anisync` (or your preferred database name).
* `MAL_CLIENT_ID`: Your MyAnimeList API client ID.
* `MAL_CLIENT_SECRET`: Your MyAnimeList API client secret.
* `ANILIST_CLIENT_ID`: Your AniList API client ID.
* `PROXY_URL` (Optional): Your HTTP/HTTPS/SOCKS5 proxy URL (e.g. `http://username:password@proxyhost.com:8080` or container-specific proxies like `http://glider:8443` if using a local Gluetun VPN container on your VPS).

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



## ⚠️ Disclaimer

**AniSync** is a tool for synchronizing progress and managing metadata from anime tracking services. It does not host, store, or distribute any media or video content. The developer does not endorse or promote access to copyrighted content. Users are solely responsible for complying with all applicable laws and the terms of service of any addons or services they use with AniSync.

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.
