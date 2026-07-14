import os
import httpx
import asyncio

async def get_openrouter_embedding():
    url = "https://openrouter.ai/api/v1/embeddings"
    
    headers = {
        "Authorization": f"Bearer ",
        "Content-Type": "application/json",
        "HTTP-Referer": url
    }
    
    payload = {
        "model": "qwen/qwen3-embedding-8b",
        "input": "Your text string goes here",
        "encoding_format": "float"
    }

    print('test 1')
    # Use an async client context manager for optimal connection pooling
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
    print('test 2')
    # Check for HTTP errors (e.g., 401 Unauthorized, 500 Server Error)
    response.raise_for_status()
    print('test 3')
    data = response.json()
    return data["data"][0]["embedding"], data

# How to run an async function in a standalone script
if __name__ == "__main__":
    embedding, data = asyncio.run(get_openrouter_embedding())
    print(f"Retrieved embedding vector length: {len(embedding)}")
    print(f"Data: {data}")