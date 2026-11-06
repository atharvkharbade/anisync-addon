import json
import logging

from app.services.http import get_client
from config import Config

logger = logging.getLogger(__name__)


def _build_prompt(
    sorted_user_history: list[dict],
    candidate_lists: dict[str, list[dict]],
) -> tuple[str, dict[str, dict]]:
    """Builds user watch history and candidate anime prompt for AI recommendation models."""
    candidates_by_name = {}
    for item_list in candidate_lists.values():
        for item in item_list[:8]:
            if item.get("name") and item["name"] not in candidates_by_name:
                candidates_by_name[item["name"]] = item

    if not candidates_by_name:
        return "", {}

    history_lines = []
    for show in sorted_user_history[:15]:
        status = show["status"].lower() if show.get("status") else "watched"
        rating_str = f"rated {show['rating']}/10" if show.get("rating") else "no rating"
        history_lines.append(f"- {show.get('title', 'Unknown')} ({status}, {rating_str})")
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
    return prompt.strip(), candidates_by_name


def _apply_explanations(
    candidate_lists: dict[str, list[dict]],
    ai_explanations: dict[str, str],
) -> dict[str, list[dict]]:
    """Attaches AI-generated explanations to anime descriptions in candidate lists."""
    if not ai_explanations or not isinstance(ai_explanations, dict):
        return candidate_lists

    def enhance_list(items):
        enhanced = []
        others = []
        for item in items:
            name = item.get("name")
            if name in ai_explanations:
                item_copy = item.copy()
                ai_desc = str(ai_explanations[name]).strip()
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


async def enhance_recommendations_with_gemini(
    gemini_api_key: str,
    sorted_user_history: list[dict],
    candidate_lists: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """
    Enhance recommendation lists using Google Gemini API with fallback models.
    Passes API key securely via x-goog-api-key header to eliminate cleartext URL logging.
    Prioritizes fast free-tier lite models first for sub-second generation.
    """
    if not gemini_api_key or not gemini_api_key.strip():
        return candidate_lists

    prompt, candidates_by_name = _build_prompt(sorted_user_history, candidate_lists)
    if not prompt or not candidates_by_name:
        return candidate_lists

    try:
        models = [
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-2.5-flash",
            "gemini-3.8-flash",
            "gemini-3.7-flash",
        ]
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        headers = {
            "x-goog-api-key": gemini_api_key.strip(),
            "Content-Type": "application/json",
        }
        client = get_client()
        resp = None
        for model in models:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                r = await client.post(url, json=payload, headers=headers, timeout=15.0)
                if r.status_code == 200:
                    resp = r
                    logger.info("Successfully generated AI recommendations via Gemini model %s", model)
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
            return _apply_explanations(candidate_lists, ai_explanations)
        elif resp:
            logger.warning("Gemini API call failed with status %s", resp.status_code)
    except Exception as e:
        logger.warning("Failed to enhance recommendations with Gemini: %s", e)

    return candidate_lists


async def enhance_recommendations_with_deepseek(
    deepseek_api_key: str,
    sorted_user_history: list[dict],
    candidate_lists: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """
    Enhance recommendation lists using DeepSeek official API (deepseek-chat).
    Passes API key securely via Authorization: Bearer header.
    Returns enhanced candidate_lists with personalized AI explanations attached.
    """
    if not deepseek_api_key or not deepseek_api_key.strip():
        return candidate_lists

    prompt, candidates_by_name = _build_prompt(sorted_user_history, candidate_lists)
    if not prompt or not candidates_by_name:
        return candidate_lists

    try:
        url = "https://api.deepseek.com/chat/completions"
        headers = {
            "Authorization": f"Bearer {deepseek_api_key.strip()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": Config.DEEPSEEK_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an advanced anime recommendation assistant that outputs raw JSON.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 3000,
        }
        client = get_client()
        resp = await client.post(url, json=payload, headers=headers, timeout=30.0)

        if resp.status_code == 200:
            res_json = resp.json()
            content = res_json["choices"][0]["message"]["content"]
            ai_explanations = json.loads(content)
            logger.info("Successfully generated AI recommendations via DeepSeek %s", Config.DEEPSEEK_MODEL)
            return _apply_explanations(candidate_lists, ai_explanations)
        else:
            logger.warning("DeepSeek API call failed with status %s: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("Failed to enhance recommendations with DeepSeek: %s", e)

    return candidate_lists
