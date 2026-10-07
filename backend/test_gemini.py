import asyncio
import httpx
from app.core.config import settings


async def main():
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "Say hello in one sentence."}
                ]
            }
        ]
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            headers={"x-goog-api-key": settings.GEMINI_API_KEY},
            json=payload
        )

        print("STATUS:", response.status_code)
        print("RESPONSE:", response.text[:2000])


asyncio.run(main())