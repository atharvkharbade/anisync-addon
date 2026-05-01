from pymongo import MongoClient
import httpx
import asyncio

async def main():
    client = MongoClient("mongodb://mongo:27017")
    db = client.get_database("anisync")
    users_col = db.get_collection("users")
    
    user = users_col.find_one()
    if not user:
        print("No user found in DB!")
        return
        
    print(f"Found user: {user.get('name')} (UID: {user.get('uid')})")
    
    token = user.get("mal_access_token")
    if not token:
        print("No MAL access token for user!")
        return
        
    headers = {"Authorization": f"Bearer {token}"}
    url = "https://api.myanimelist.net/v2/users/@me/animelist"
    params = {
        "fields": "id,title,status,my_list_status{status,num_episodes_watched}",
        "limit": 100,
        "status": "watching"
    }
    
    async with httpx.AsyncClient() as http_client:
        resp = await http_client.get(url, headers=headers, params=params)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            print(f"\n--- MAL Watching List ({len(data)} items) ---")
            for idx, item in enumerate(data):
                node = item["node"]
                print(f"{idx+1}. {node.get('title')} (ID: {node.get('id')}) - status: {node.get('status')} - watched: {node.get('my_list_status', {}).get('num_episodes_watched')}")
        else:
            print(f"MAL Request failed: {resp.status_code} - {resp.text}")

if __name__ == "__main__":
    asyncio.run(main())
