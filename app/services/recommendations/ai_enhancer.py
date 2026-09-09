import json
import logging

from app.services.http import get_client

logger = logging.getLogger(__name__)


async def enhance_recommendations_with_gemini(
    gemini_api_key: str,
    sorted_user_history: list[dict],
    candidate_lists: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """
    Enhance recommendation lists using Google Gemini API with fallback models.
    Takes candidate_lists mapping category names (e.g. 'top_picks', 'loved_items') to lists of dict items.
    Returns enhanced candidate_lists with personalized AI explanations attached to descriptions.
    """
    if not gemini_api_key:
        return candidate_lists

    candidates_by_name = {}
    for item_list in candidate_lists.values():
        for item in item_list[:8]:
            if item.get("name") and item["name"] not in candidates_by_name:
                candidates_by_name[item["name"]] = item

    if not candidates_by_name:
        return candidate_lists

    history_lines = []
    for show in sorted_user_history[:15]:
        status = show["status"].lower() if show["status"] else "watched"
        rating_str = f"rated {show['rating']}/10" if show["rating"] else "no rating"
        history_lines.append(f"- {show['title']} ({status}, {rating_str})")
    history_text = "\n".join(history_lines)
    candidates_text = "\n".join([f"- {name}" for name in candidates_by_name.keys()])

    prompt = f"""
    You are an advanced anime recommendation assistant.
    Based on the user's anime watch history:
    {history_text}

    And this list of candidate anime recommendations:
    {candidates_text}

    For each candidate that is relevant, write a personalized, engaging 1-sentence description explaining why the user would like it based on their history (referencing specific anime they watched when appropriate). Keep descriptions concise (under 150 characters).

    Return your response as a JSON object mapping the exact candidate title to its personalized description:
    {{
      "Anime Title 1": "Description...",
      "Anime Title 2": "Description...",
      ...
    }}
    Return only the raw JSON.
    """

    try:
        models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        client = get_client()
        resp = None
        for model in models:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_api_key}"
                r = await client.post(url, json=payload, timeout=15)
                if r.status_code == 200:
                    resp = r
                    logger.info("Successfully generated AI recommendations via %s", model)
                    break
                else:
                    logger.warning(
                        "Gemini model %s returned status %s, attempting next fallback model...",
                        model,
                        r.status_code,
                    )
            except Exception as model_err:
                logger.warning("Gemini model %s failed (%s), attempting next fallback model...", model, model_err)

        if resp and resp.status_code == 200:
            res_json = resp.json()
            text = res_json["candidates"][0]["content"]["parts"][0]["text"]
            ai_explanations = json.loads(text)

            def enhance_list(items):
                enhanced = []
                others = []
                for item in items:
                    name = item.get("name")
                    if name in ai_explanations:
                        item_copy = item.copy()
                        ai_desc = ai_explanations[name]
                        syn = item_copy.get("synopsis") or ""
                        item_copy["description"] = f"{ai_desc}  \n\n{syn}" if syn else ai_desc
                        enhanced.append(item_copy)
                    else:
                        others.append(item)
                return enhanced + others

            enhanced_result = {}
            for key, items in candidate_lists.items():
                enhanced_result[key] = enhance_list(items)
            return enhanced_result
        else:
            if resp:
                logger.warning("Gemini API call failed with status %s: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("Failed to enhance recommendations with Gemini: %s", e)

    return candidate_lists
