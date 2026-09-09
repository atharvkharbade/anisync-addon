DISCOVERY_CAT_IDS = [
    "anisync_trending",
    "anisync_highest_rated",
    "anisync_most_popular",
    "anisync_top_airing",
    "anisync_seasonal",
    "anisync_schedule",
    "anisync_spotlight",
]


def build_anilist_discovery_query(cur_season: str, next_season: str, year: int, next_year: int) -> str:
    return f"""
    fragment MediaFields on Media {{
      id
      idMal
      genres
      format
      duration
      averageScore
      popularity
      episodes
      startDate {{ year }}
      seasonYear
      title {{
        english
        userPreferred
        romaji
      }}
      coverImage {{
        large
      }}
      bannerImage
      description
      nextAiringEpisode {{
        airingAt
        episode
        timeUntilAiring
      }}
    }}
    query {{
      trending: Page(page: 1, perPage: 50) {{
        media(type: ANIME, sort: TRENDING_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      highestRated: Page(page: 1, perPage: 50) {{
        media(type: ANIME, sort: SCORE_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      mostPopular: Page(page: 1, perPage: 50) {{
        media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      topAiring: Page(page: 1, perPage: 50) {{
        media(type: ANIME, status: RELEASING, sort: SCORE_DESC, popularity_greater: 2000, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonal: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: {cur_season}, seasonYear: {year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalNext: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: {next_season}, seasonYear: {next_year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalUpcoming: Page(page: 1, perPage: 50) {{
        media(type: ANIME, status: NOT_YET_RELEASED, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalWinter: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: WINTER, seasonYear: {year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalSpring: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: SPRING, seasonYear: {year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalSummer: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: SUMMER, seasonYear: {year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      seasonalFall: Page(page: 1, perPage: 50) {{
        media(type: ANIME, season: FALL, seasonYear: {year}, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      schedule: Page(page: 1, perPage: 50) {{
        media(type: ANIME, status: RELEASING, sort: POPULARITY_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      spotlightMovies: Page(page: 1, perPage: 50) {{
        media(type: ANIME, format: MOVIE, sort: SCORE_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      spotlightNewMovies: Page(page: 1, perPage: 50) {{
        media(type: ANIME, format: MOVIE, sort: START_DATE_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      spotlightOva: Page(page: 1, perPage: 50) {{
        media(type: ANIME, format_in: [OVA, SPECIAL], sort: SCORE_DESC, isAdult: false) {{
          ...MediaFields
        }}
      }}
      spotlightClassics: Page(page: 1, perPage: 50) {{
        media(type: ANIME, startDate_lesser: 20120101, sort: SCORE_DESC, popularity_greater: 10000, isAdult: false) {{
          ...MediaFields
        }}
      }}
    }}
    """
