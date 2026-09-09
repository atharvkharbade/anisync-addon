import unittest
from app.services.poster import (
    get_poster_url,
    get_rpdb_poster_url,
    validate_rpdb_api_key,
    validate_top_poster_api_key,
    build_rpdb_poster_url,
    build_top_poster_url,
    build_custom_poster_url,
    resolve_media_ids,
)
from app.services.poster_service import (
    get_poster_url as facade_get_poster_url,
    get_rpdb_poster_url as facade_get_rpdb_poster_url,
    validate_rpdb_api_key as facade_validate_rpdb_api_key,
    validate_top_poster_api_key as facade_validate_top_poster_api_key,
)


class TestPosterSubpackage(unittest.TestCase):
    def test_facade_equality(self):
        """Verify that poster_service facade perfectly mirrors app.services.poster."""
        self.assertIs(get_poster_url, facade_get_poster_url)
        self.assertIs(get_rpdb_poster_url, facade_get_rpdb_poster_url)
        self.assertIs(validate_rpdb_api_key, facade_validate_rpdb_api_key)
        self.assertIs(validate_top_poster_api_key, facade_validate_top_poster_api_key)
        self.assertIs(get_poster_url, get_rpdb_poster_url)

    def test_build_rpdb_poster_url(self):
        url = build_rpdb_poster_url(
            rpdb_key="t0-free-rpdb",
            media_type="series",
            shape="poster",
            imdb_id="tt1234567",
        )
        self.assertIn("api.ratingposterdb.com/t0-free-rpdb/imdb/poster-default/tt1234567.jpg", url)
        self.assertIn("fallback=true", url)

        # Landscape
        url_land = build_rpdb_poster_url(
            rpdb_key="t0-free-rpdb",
            media_type="series",
            shape="landscape",
            imdb_id="tt1234567",
        )
        self.assertIn("api.ratingposterdb.com/t0-free-rpdb/imdb/backdrop-default/tt1234567.jpg", url_land)

    def test_build_top_poster_url(self):
        url = build_top_poster_url(
            top_key="TP-key123",
            media_type="series",
            shape="poster",
            imdb_id="tt1234567",
        )
        self.assertEqual(url, "https://top-posters.com/TP-key123/imdb/poster-default/tt1234567.jpg")

        url_land = build_top_poster_url(
            top_key="TP-key123",
            media_type="movie",
            shape="landscape",
            tmdb_id="999",
        )
        self.assertEqual(url_land, "https://top-posters.com/TP-key123/tmdb/backdrop-default/movie-999.jpg")

    def test_build_custom_poster_url(self):
        pattern = "https://custom.art/{shape}/{endpoint}/{imdb_id}.jpg"
        url = build_custom_poster_url(
            custom_pattern=pattern,
            shape="landscape",
            fallback_poster="https://example.com/clean.jpg",
            imdb_id="tt1234567",
        )
        self.assertEqual(url, "https://custom.art/landscape/backdrop-default/tt1234567.jpg")

        # Landscape fallback when missing placeholder
        no_land_pattern = "https://custom.art/posters/{imdb_id}.jpg"
        url_fallback = build_custom_poster_url(
            custom_pattern=no_land_pattern,
            shape="landscape",
            fallback_poster="https://example.com/clean.jpg",
            imdb_id="tt1234567",
        )
        self.assertEqual(url_fallback, "https://example.com/clean.jpg")

    def test_get_poster_url_dispatch(self):
        user_rpdb = {
            "poster_provider": "rpdb",
            "rpdb_api_key": "t0-free-rpdb",
            "rpdb_key_valid": True,
        }
        url = get_poster_url(
            user=user_rpdb,
            media_type="series",
            imdb_id="tt1234567",
            fallback_poster="https://example.com/clean.jpg",
        )
        self.assertIn("api.ratingposterdb.com/t0-free-rpdb/imdb/poster-default/tt1234567.jpg", url)

        # Per-catalog override to clean
        clean_url = get_poster_url(
            user=user_rpdb,
            media_type="series",
            imdb_id="tt1234567",
            fallback_poster="https://example.com/clean.jpg",
            provider_override="clean",
        )
        self.assertEqual(clean_url, "https://example.com/clean.jpg")


if __name__ == "__main__":
    unittest.main()
