import asyncio
from bot.pipeline.images import fetch_reddit_images, fetch_og_images, _validate_image, _fetch_reddit_json

async def test():
    # Test 1: Reddit JSON API
    print("=== Test 1: Reddit JSON API ===")
    data = await _fetch_reddit_json("https://www.reddit.com/r/singularity/comments/1lm0c9h/gemini_25_pro_retakes_1_on_lmarena/")
    if data:
        is_gallery = data.get("is_gallery", False)
        print(f"  is_gallery: {is_gallery}")
        preview = data.get("preview", {})
        images = preview.get("images", [])
        if images:
            src = images[0].get("source", {}).get("url", "N/A")
            print(f"  source URL: {src[:100]}...")
        direct = data.get("url", "")
        print(f"  direct URL: {direct}")
    else:
        print("  Failed to fetch JSON (may be rate-limited)")

    # Test 2: Image validation (Pillow check on invalid URL)
    print()
    print("=== Test 2: Image validation (bad URL) ===")
    ok = await _validate_image("https://i.redd.it/nonexistent-placeholder-test-12345.jpg")
    print(f"  Invalid URL returns: {ok} (expected False)")

    # Test 3: OG multi-image extraction from a real page
    print()
    print("=== Test 3: OG multi-image from openai.com ===")
    try:
        urls = await fetch_og_images("https://openai.com/index/introducing-gpt-4o/")
        print(f"  Found {len(urls)} image(s)")
        for u in urls[:2]:
            print(f"    {u[:100]}")
    except Exception as e:
        print(f"  Error (may be blocked): {e}")

    # Test 4: Full Reddit image extraction pipeline
    print()
    print("=== Test 4: Reddit images pipeline ===")
    try:
        imgs = await fetch_reddit_images(
            "<p>test content</p>",
            "https://www.reddit.com/r/singularity/comments/1lm0c9h/gemini_25_pro_retakes_1_on_lmarena/"
        )
        print(f"  Found {len(imgs)} image(s)")
        for i, url in enumerate(imgs):
            print(f"    [{i+1}] {url[:100]}")
    except Exception as e:
        print(f"  Error: {e}")

    print()
    print("=== All tests completed ===")

asyncio.run(test())
