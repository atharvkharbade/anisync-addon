<p align="center">
  <img src="docs/images/logo.png" width="120" alt="AniSync Logo" />
</p>

# AniSync - MyAnimeList, AniList & Simkl Tracker for Stremio

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg?logo=python&logoColor=white)](https://python.org)
[![Quart Version](https://img.shields.io/badge/quart-0.20.0+-00b4d8.svg)](https://pgjones.gitlab.io/quart/)
[![Docker Support](https://img.shields.io/badge/docker-ready-2496ed.svg?logo=docker&logoColor=white)](https://www.docker.com)
[![Stremio Addon](https://img.shields.io/badge/stremio-addon-8a2be2.svg)](https://stremio.com)

**AniSync** is a power-user-focused Stremio addon that automatically synchronizes your anime progress with MyAnimeList, AniList and Simkl in real-time. It enriches Stremio with poster badges (including support for RPDB overlays), airing indicators, episode filler & watched tags and personalized catalogs directly synced with your watchlists.

---

## 🌟 Features

### 📺 Poster Badges & Airing Indicators
Whenever a new episode drops for a show on your watchlist, AniSync overlays a clean `NEW EPISODE` banner directly on the poster in Stremio. If you connect multiple trackers, it overlays MyAnimeList, AniList and Simkl logos side-by-side.
* **RPDB Integration**: Optionally supply a Rating Poster DB (RPDB) API key to overlay rating logos directly on your posters.

![Combined Tracker Poster Badges](docs/images/stremio_poster_badges.png)

### 🗂️ Combined Watchlist Catalogs
Connect MyAnimeList, AniList and Simkl simultaneously. AniSync merges and deduplicates your lists into single, clean catalogs in Stremio, auto-merging progress across trackers and supporting AniList re-watching series.

![Stremio Combined Catalogs](docs/images/stremio_combined_watchlists.png)

### 🤖 Personalized Anime Recommendations
Get custom anime recommendation rows injected directly into Stremio based on your watch history and tastes.
* **Gemini AI Enhancement**: Optionally supply a Gemini API key to personalize recommendation titles and generate custom themed lists.

![Stremio Recommendations](docs/images/stremio_gemini_recs.png)

### 🚫 Episode Filler Indicators (`[Filler]`)
Fetches episode lists via the Jikan API and prepends a `[Filler]` tag directly to the episode titles in Stremio's player detail overlay, letting you know exactly which episodes are safe to skip.

![Inline episode filler tag details](docs/images/stremio_filler_indicators.png)

### ✅ Episode Watched Indicators (`[Watched]`)
A unique feature that prepends a `[Watched]` tag directly to the episode titles in Stremio for all episodes you have already completed. By reading your watch progress from MyAnimeList, AniList or Simkl, AniSync lets you see exactly where you left off at a glance inside Stremio's player and detail views.

![Inline episode watched tag details](docs/images/stremio_watched_indicators.png)

---

## 📥 Installation

1. Visit the **[AniSync Configuration Dashboard](https://yourdomain.com)** (replace with your hosted instance URL).
2. Authenticate with **MyAnimeList**, **AniList** and/or **Simkl** via secure OAuth2.
3. Save your preferences and click **Direct Install** or copy the **Manifest URL** into Stremio.

---

## 🛠️ Self-Hosting

### Docker Compose

You can easily deploy AniSync using Docker. Create a `.env` file from `.env.example` (which includes configuration for general and domain-specific SOCKS5/HTTP proxies to bypass tracker rate limits) and run:

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

**AniSync** is a tool for synchronizing progress and managing metadata from anime tracking services. It does not host, store or distribute any media or video content. The developer does not endorse or promote access to copyrighted content. Users are solely responsible for complying with all applicable laws and the terms of service of any addons or services they use with AniSync.

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.
