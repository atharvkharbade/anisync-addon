import unittest

from app.services.recommendations import (
    clean_html,
    is_proper_anime,
    normalize_user_status,
    select_weighted_seeds,
    get_cached_recommendations,
    get_popular_fallbacks,
    trigger_recommendation_update_background,
    trigger_popular_fallbacks_update_background,
    get_recommendations_for_seeds,
    generate_genre_recommendations,
    update_recommendations_cache,
)
from app.services.recommendations.utils import (
    clean_html as u_clean_html,
    is_proper_anime as u_is_proper_anime,
    normalize_user_status as u_normalize_user_status,
)
from app.services.recommendations.fetchers import (
    get_mal_recommendations_for_id,
    get_anilist_recommendations_bulk,
    get_top_anime_by_genre,
)
from app.services.recommendations.ai_enhancer import enhance_recommendations_with_gemini
from app.services.recommendations.engine import (
    select_weighted_seeds as e_select_weighted_seeds,
    get_recommendations_for_seeds as e_get_recommendations_for_seeds,
    generate_genre_recommendations as e_generate_genre_recommendations,
)
from app.services.recommendations.cache import (
    get_cached_recommendations as c_get_cached_recommendations,
    get_popular_fallbacks as c_get_popular_fallbacks,
)


class TestRecommendationsSubpackage(unittest.TestCase):
    def test_reexport_integrity(self):
        """Verify that app.services.recommendations properly re-exports symbols from submodules."""
        self.assertIs(clean_html, u_clean_html)
        self.assertIs(is_proper_anime, u_is_proper_anime)
        self.assertIs(normalize_user_status, u_normalize_user_status)
        self.assertIs(select_weighted_seeds, e_select_weighted_seeds)
        self.assertIs(get_recommendations_for_seeds, e_get_recommendations_for_seeds)
        self.assertIs(generate_genre_recommendations, e_generate_genre_recommendations)
        self.assertIs(get_cached_recommendations, c_get_cached_recommendations)
        self.assertIs(get_popular_fallbacks, c_get_popular_fallbacks)

    def test_clean_html(self):
        raw = "<p>This is &quot;great&quot; &amp; fun!</p>"
        self.assertEqual(clean_html(raw), 'This is "great" & fun!')
        self.assertEqual(clean_html(""), "")
        self.assertEqual(clean_html(None), "")

    def test_is_proper_anime(self):
        self.assertTrue(is_proper_anime("Attack on Titan"))
        self.assertFalse(is_proper_anime("Attack on Titan Recap"))
        self.assertFalse(is_proper_anime("Attack on Titan PV"))
        self.assertFalse(is_proper_anime("Attack on Titan Picture Drama"))
        self.assertFalse(is_proper_anime("AoT Chibi Theatre"))
        self.assertFalse(is_proper_anime("Some Show", synopsis="A recap of the previous events"))

    def test_normalize_user_status(self):
        self.assertEqual(normalize_user_status("watching"), "watching")
        self.assertEqual(normalize_user_status("current"), "watching")
        self.assertEqual(normalize_user_status("completed"), "completed")
        self.assertEqual(normalize_user_status("on_hold"), "on_hold")
        self.assertEqual(normalize_user_status("paused"), "on_hold")
        self.assertEqual(normalize_user_status("dropped"), "dropped")
        self.assertEqual(normalize_user_status("plan_to_watch"), "planning")
        self.assertEqual(normalize_user_status("planning"), "planning")
        self.assertEqual(normalize_user_status(None), "watching")

    def test_select_weighted_seeds(self):
        pool = [
            {"id": 1, "title": "Top Show", "rating": 10},
            {"id": 2, "title": "Good Show", "rating": 8},
            {"id": 3, "title": "Mid Show", "rating": 5},
            {"id": 4, "title": "Unrated Show", "rating": 0},
        ]
        # Requesting more or equal count returns full pool
        self.assertEqual(len(select_weighted_seeds(pool, 4)), 4)
        self.assertEqual(len(select_weighted_seeds(pool, 10)), 4)

        # Sampling subset returns requested count
        sampled = select_weighted_seeds(pool, 2)
        self.assertEqual(len(sampled), 2)


if __name__ == "__main__":
    unittest.main()
