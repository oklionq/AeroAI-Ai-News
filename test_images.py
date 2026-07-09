import asyncio
from bot.pipeline.images import fetch_og_image, fetch_reddit_image
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
            img = await fetch_reddit_image(item['summary'], item['url'])
            if img:
                print(f"SUCCESS: Found image for {item['url']} -> {img}")
                found = True
                break
        if not found:
            print(f"WARNING: No images found in recent posts from {s_name}")

async def main():
    urls = [
        "https://openai.com/index/government-national-security-partnerships/",
        "https://huggingface.co/blog/gemma",
        "https://techcrunch.com/"
    ]
    for url in urls:
        print(f"Testing {url} ...")
        res = await fetch_og_image(url)
        print(f"Result: {res}")
        
    await test_reddit_smoke()

if __name__ == '__main__':
    asyncio.run(main())
