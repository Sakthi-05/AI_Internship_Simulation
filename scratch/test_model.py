import httpx
import re

key = "AQ.Ab8RN6LmFEwbDZ5fqx6CimiWk_uV4_HHwRe7bZZdBnlsBiSL9g"

models = [
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
]

system_prompt = "You are the rubric-based evaluator for a baseline assessment. Return only strict JSON."
prompt = "Evaluate this baseline assessment answer. Return JSON: {\"evaluations\": [{\"question_id\": \"q1\", \"score\": 85, \"explanation\": \"Good\", \"feedback\": \"Keep it up\"}]}"

for m in models:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}"
    contents = [
        {"role": "user", "parts": [{"text": f"[SYSTEM]\n{system_prompt}"}]},
        {"role": "user", "parts": [{"text": prompt}]}
    ]
    gen = {
        "maxOutputTokens": 4096,
        "responseMimeType": "application/json"
    }
    if not re.match(r"gemini-3\.(5|6|8)-", m):
        gen["temperature"] = 0.7
    try:
        r = httpx.post(url, json={"contents": contents, "generationConfig": gen}, timeout=15.0)
        print(f"Model {m}: {r.status_code}")
        if r.status_code == 200:
            print("  SUCCESS!")
            break
        else:
            print("  Body:", r.text[:150])
    except Exception as e:
        print(f"Model {m} error:", e)
