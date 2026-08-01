<p align="center">
  <img src="docs/images/logo.png" width="120" alt="AniSync Logo" />
</p>

# AniSync - MyAnimeList, AniList & Simkl Tracker for Stremio

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg?logo=python&logoColor=white)](https://python.org)
[![Quart Version](https://img.shields.io/badge/quart-0.20.0+-00b4d8.svg)](https://pgjones.gitlab.io/quart/)
[![Docker Support](https://img.shields.io/badge/docker-ready-2496ed.svg?logo=docker&logoColor=white)](https://www.docker.com)
[![Stremio Addon](https://img.shields.io/badge/stremio-addon-8a2be2.svg)](https://stremio.com)

**AniSync** is a power-user-focused Stremio addon that seamlessly bridges your anime tracking experience across **MyAnimeList**, **AniList**, and **Simkl**. It enriches Stremio with real-time watchlist synchronization, customizable multi-tracker poster badges, multi-provider metadata (Kitsu, MAL, AniList), title language localization, progress-aware filler warnings, live airing countdowns, and personalized recommendations.

---

## 🌟 Features

### 📺 Multi-Tracker Poster Badges & `NEW EPISODE` Overlays
Whenever a new episode drops for a show on your watchlist, AniSync overlays an eye-catching `NEW EPISODE` indicator directly on the poster in Stremio. If you connect multiple tracking services, AniSync displays side-by-side tracker logos (**MAL**, **AniList**, **Simkl**).

Choose between two distinct aesthetic overlay designs in your configuration:
* **Modern Design**: Sleek bottom pill badges with compact tracker logos.
* **Classic Design**: High-contrast top header ribbon with distinct bottom tracker badges.
* **RPDB Integration**: Optionally supply your Rating Poster DB (RPDB) API key to overlay rating badges directly on posters.

<p align="center">
  <b>Modern Design</b><br>
  <img src="docs/images/New_Episode_Overlay_Modern.png" alt="Modern Poster Badges" width="95%" />
</p>

<p align="center">
  <b>Classic Design</b><br>
  <img src="docs/images/New_Episode_Overlay_Classic.png" alt="Classic Poster Badges" width="95%" />
</p>

---

### 🗂️ Synchronized Watchlist Catalogs
Connect MyAnimeList, AniList, and Simkl simultaneously. AniSync deduplicates and organizes your anime into clean, unified catalogs:
* **Combined Watchlists**: Merges *Watching*, *Plan to Watch*, *Completed*, *On Hold*, and *Dropped* across all connected accounts.
* **Granular Catalog Manager**: Enable, disable, or reorder combined catalogs vs. dedicated individual tracker rows to tailor your Stremio home screen.
* **Multi-Account Auto-Merge**: Automatically merges progress across trackers and handles AniList re-watching series seamlessly.

![Stremio Combined Catalogs](docs/images/Combined_Tracker_Watchlist.png)

---

### 🤖 Intelligent Personalized Recommendations (100% Explained)
Discover new anime with personalized recommendation rows injected directly into Stremio:
* **100% Recommendation Reasons**: Every suggested anime explains *why* it appears on your home screen (e.g. *"Inspired by your favorites: Hunter x Hunter"*, *"Popular Dark Fantasy based on your taste"*, or *"Popular community recommendation"*).
* **Gemini AI Natural Language Explanations**: Optionally connect a Google Gemini API key for natural language suggestions tailored to your unique taste.
* **5 Dedicated Rows**: *Top Picks for You*, *Inspired by your Favorites*, *More from your Watchlist*, *Because you Watched [Anime]*, and *Curated Genre Collections*.

![Stremio Recommendations](docs/images/Anime_Recommendations.png)

---

### 🚫 Progress-Aware Filler Arc Warnings & Inline `[Filler]` Tags
Never wonder if an episode is canon again:
* **Dynamic Filler Arc Notices**: Injects intelligent progress-aware alerts directly into the series summary:
  * `[Current Filler Arc: Episodes 101–106]` if you are currently watching inside a filler block.
  * `[Upcoming Filler Arc: Episodes 101–106]` if a filler arc approaches within your next 10 episodes.
  * `[Filler Guide: ...]` if you are starting a show with standalone filler episodes.
* **Inline Episode Tags**: Shows a `[Filler]` tag directly beside episode titles in Stremio's player and season list.

![Inline episode filler tag details](docs/images/Episode_Filler.png)

---

### ✅ Episode Watched Indicators (`[Watched]`)
Prepends a `[Watched]` badge to completed episodes in Stremio by reading your synchronized progress from MyAnimeList, AniList, or Simkl. Instantly see where you left off without opening external tracking apps.

![Inline episode watched tag details](docs/images/Watched_Episode.png)

---

### ⚡ Multi-Provider Metadata Engine
Choose your preferred anime database:
* **Kitsu (Default)**: Rock-solid foundational metadata and high-speed streaming resolution.
* **MyAnimeList (MAL)**: Full MAL synopsis, community scores, and official studio descriptions.
* **AniList**: AniList synopsis formatting, average scores, and vibrant backdrop banners.
* *Universal Fallback*: All streaming resolution stays fully compatible with Stremio's torrent and streaming addons.

---

### 🌐 Universal Title Language Localization
Switch titles addon-wide with one click in `/configure`:
* **English**: Official localized English titles (e.g. *Attack on Titan*, *Case Closed*).
* **Romaji**: Standard Romanized Japanese titles (e.g. *Shingeki no Kyojin*, *Meitantei Conan*).
* **Japanese (Native)**: Original Kanji/Kana titles (e.g. *進撃の巨人*, *名探偵コナン*).

---

### ⏳ Real-Time Next Airing Countdowns
Stay on top of weekly episode releases:
* Injects a live `[Next Airing: Episode X releases in Y days]` schedule header into the synopsis of currently airing anime.
* Powered by live AniList GraphQL & MAL broadcast feeds.
* Intelligently suppresses stale historical broadcast schedules on completed series.

---

### 🧭 Rich Discovery Catalogs
Explore trending and top-rated anime curated directly from the anime community:
* **AniList Trending Now**: The hottest anime currently gaining popularity.
* **AniList All-Time Popular**: The most popular anime across the entire AniList database.
* **MyAnimeList Top Airing**: Highest-rated anime currently airing this season.
* **MyAnimeList Most Popular**: Most added and watched anime on MyAnimeList.

---

### 🚀 Status-Aware Caching Architecture
AniSync features an intelligent dual-tier MongoDB caching engine:
* **Airing / Releasing Anime (2-Hour TTL)**: Ensures new episode drops, filler tags, and airing countdowns update immediately.
* **Completed Anime (7-Day TTL)**: Accelerates massive 1,000+ episode series (like *One Piece* and *Detective Conan*) by **~90%** (17s ➡️ <1.9s) for instantaneous loading.

---

## 📥 Installation

1. Visit the **[AniSync Configuration Dashboard](https://anisync.qzz.io)**
2. Authenticate with **MyAnimeList**, **AniList**, and/or **Simkl**.
3. Customize your preferred metadata provider, title language, poster overlay style, and catalog rows.
4. Click **Install Addon** or copy the **Manifest URL** into Stremio.

---

## 🛠️ Self-Hosting

### 1. Environment Configuration

Create a `.env` file based on `.env.example`:

```env
# ── App & Security ─────────────────────────────────────────────────────────
SECRET_KEY=generate_a_secure_random_string_here
FLASK_DEBUG=0
FLASK_RUN_HOST=yourdomain.com

# ── MongoDB ───────────────────────────────────────────────────────────────
MONGO_URI=mongodb://mongo:27017
MONGO_DB=anisync

# ── Tracker OAuth Credentials ─────────────────────────────────────────────
# MyAnimeList (https://myanimelist.net/apiconfig)
MAL_CLIENT_ID=your_mal_client_id
MAL_CLIENT_SECRET=your_mal_client_secret

# AniList (https://anilist.co/settings/developer)
ANILIST_CLIENT_ID=your_anilist_client_id
ANILIST_CLIENT_SECRET=your_anilist_client_secret

# Simkl (https://simkl.com/settings/developer)
SIMKL_CLIENT_ID=your_simkl_client_id
SIMKL_CLIENT_SECRET=your_simkl_client_secret

# ── Proxy & Rate-Limit Mitigation (Optional) ──────────────────────────────
# Route API requests through HTTP, HTTPS, or SOCKS5 proxies
PROXY_URL=
PROXY_MAL=
PROXY_ANILIST=
PROXY_SIMKL=
PROXY_JIKAN=
PROXY_KITSU=
PROXY_ANIZP=
```

### 2. Docker Compose

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

---

## ⚠️ Disclaimer

**AniSync** is a tool for synchronizing watch progress and organizing metadata from anime tracking services. It does not host, store, stream, or distribute any media or video content. Users are solely responsible for complying with the terms of service of any third-party services used in conjunction with AniSync.

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.
