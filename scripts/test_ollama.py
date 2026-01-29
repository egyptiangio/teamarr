"""Test Ollama models for stream parsing."""
import httpx
import json
import time

OLLAMA_URL = "http://192.168.10.5:11434"

TEST_STREAMS = [
    "US: VIAPLAY PPV 3 - NHL COLUMBUS - PHILADELPHIA | Thu 29 Jan 01:35 | 8K EXCLUSIVE",
    "NBA: Los Angeles Lakers vs Golden State Warriors 7:30 PM ET",
    "ESPN+ UFC 312: Du Plessis vs. Strickland | Main Card",
    "Premier League: Manchester United @ Arsenal (ES)",
    "MLB: NYY @ BOS 1/15 7:00PM",
]

PROMPT_TEMPLATE = """Extract sports event info from this stream name. Return ONLY valid JSON.

Stream: "{stream}"

Return JSON with these fields (use null if not found):
- team1: First team name
- team2: Second team name
- league: League code (NHL, NBA, NFL, MLB, UFC, EPL, etc.)
- sport: Sport type (Hockey, Basketball, Football, Baseball, MMA, Soccer, etc.)
- date: Date string as found in stream
- time: Time string as found in stream"""


def test_model(model: str, stream: str) -> tuple[dict | None, float]:
    """Test a model with a stream name, return parsed result and time."""
    start = time.time()
    try:
        response = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": PROMPT_TEMPLATE.format(stream=stream),
                "stream": False,
                "format": "json",
            },
            timeout=60.0,
        )
        elapsed = time.time() - start
        data = response.json()
        return json.loads(data.get("response", "{}")), elapsed
    except Exception as e:
        return {"error": str(e)}, time.time() - start


def main():
    models = ["qwen2.5:3b", "qwen2.5:7b"]

    print("=" * 80)
    print("OLLAMA STREAM PARSING TEST")
    print("=" * 80)

    for model in models:
        print(f"\n### Model: {model} ###\n")
        total_time = 0

        for stream in TEST_STREAMS:
            result, elapsed = test_model(model, stream)
            total_time += elapsed

            print(f"Stream: {stream[:60]}...")
            print(f"Time: {elapsed:.2f}s")
            print(f"Result: {json.dumps(result, indent=2)}")
            print("-" * 40)

        print(f"\nTotal time for {model}: {total_time:.2f}s")
        print(f"Average per stream: {total_time/len(TEST_STREAMS):.2f}s")


if __name__ == "__main__":
    main()
