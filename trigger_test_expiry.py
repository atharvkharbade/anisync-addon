from app.services.db import users_collection

result = users_collection.update_many(
    {},
    {
        "$set": {
            "anilist_session_expired": True,
            "mal_session_expired": True,
            "simkl_session_expired": True
        }
    }
)
print(f"Successfully set expired flags to True for {result.modified_count} users.")
