from app.services.db import users_collection

result = users_collection.update_many(
    {},
    {
        "$set": {
            "anilist_token_expired": True,
            "mal_token_expired": True,
            "simkl_token_expired": True
        }
    }
)
print(f"Successfully set expired flags to True for {result.modified_count} users.")
print("To test a completely fresh/fake user, visit: https://beta.anisync.qzz.io/auth/test-login")
