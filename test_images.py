import asyncio
from bot.pipeline.images import fetch_og_image, fetch_og_images, fetch_reddit_image, fetch_reddit_images
from bot.sources.reddit import fetch_reddit

async def test_reddit_smoke():
    sources = [
        (6, "Reddit Singularity", "https://www.reddit.com/r/singularity/.rss"),
        (7, "Reddit LocalLLaMA", "https://www.reddit.com/r/LocalLLaMA/.rss"),
        (8, "Reddit OpenAI", "https://www.reddit.com/r/OpenAI/.rss"),
    ]
    
    for s_id, s_name, url in sources:
        print(f"\n--- Testing {s_name} ---")
        items = await fetch_reddit(s_id, s_name, url)
        # Find first item with image in content
        found = False
        for item in items:
            images = await fetch_reddit_images(item['summary'], item['url'])
            if images:
                print(f"SUCCESS: Found {len(images)} image(s) for {item['url']}")
                for i, img in enumerate(images):
                    print(f"  [{i+1}] {img}")
                found = True
                break
        if not found:
            print(f"WARNING: No images found in recent posts from {s_name}")

async def test_og_multi():
    urls = [
        "https://openai.com/index/government-national-security-partnerships/",
        "https://huggingface.co/blog/gemma",
        "https://techcrunch.com/"
    ]
    for url in urls:
        print(f"\nTesting OG multi-image for {url} ...")
        images = await fetch_og_images(url)
        print(f"Found {len(images)} image(s):")
        for i, img in enumerate(images):
            print(f"  [{i+1}] {img}")

async def main():
    # Legacy single-image tests
    urls = [
        "https://openai.com/index/government-national-security-partnerships/",
        "https://huggingface.co/blog/gemma",
        "https://techcrunch.com/"
    ]
    for url in urls:
        print(f"Testing {url} ...")
        res = await fetch_og_image(url)
        print(f"Result: {res}")
        
    # Multi-image tests
    await test_og_multi()
    await test_reddit_smoke()

if __name__ == '__main__':
    asyncio.run(main())
