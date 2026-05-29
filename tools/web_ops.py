"""
tools/web_ops.py — Web access tools for Selyrion.

Equivalents of: WebFetch, WebSearch
"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from scos_tools import register_tool


# ── web_fetch ─────────────────────────────────────────────────────────────────

@register_tool(
    "web_fetch",
    "Fetch the content of a URL and return its text. Strips HTML tags. Use for reading documentation, APIs, or known pages.",
    {
        "url":     {"type": "string",  "required": True,  "desc": "Full URL to fetch"},
        "timeout": {"type": "integer", "required": False, "desc": "Request timeout seconds (default 15)"},
        "max_chars":{"type":"integer", "required": False, "desc": "Max characters to return (default 8000)"},
    }
)
def web_fetch(inputs: dict) -> dict:
    try:
        import requests
    except ImportError:
        return {"status": "error", "error": "requests not installed"}

    url     = inputs["url"]
    timeout = int(inputs.get("timeout", 15))
    max_ch  = int(inputs.get("max_chars", 8000))

    try:
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "Selyrion/1.0 research-bot"})
        r.raise_for_status()
        content = r.text

        # Strip HTML tags if HTML response
        if "text/html" in r.headers.get("content-type", ""):
            content = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", content)
            content = re.sub(r"<style[^>]*>[\s\S]*?</style>",  " ", content)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s{3,}", "\n\n", content)

        return {
            "status":       "success",
            "url":          url,
            "content_type": r.headers.get("content-type", ""),
            "chars":        len(content),
            "content":      content[:max_ch],
        }
    except Exception as e:
        return {"status": "error", "url": url, "error": str(e)}


# ── web_search ────────────────────────────────────────────────────────────────

@register_tool(
    "web_search",
    "Search the web via DuckDuckGo and return titles, URLs, and snippets. Use for finding documentation, examples, or current information.",
    {
        "query":       {"type": "string",  "required": True,  "desc": "Search query"},
        "max_results": {"type": "integer", "required": False, "desc": "Max results to return (default 8)"},
    }
)
def web_search(inputs: dict) -> dict:
    try:
        import requests
    except ImportError:
        return {"status": "error", "error": "requests not installed"}

    query = inputs["query"]
    limit = int(inputs.get("max_results", 8))

    try:
        # DuckDuckGo instant answer API
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": 1, "no_html": 1},
            timeout=15,
            headers={"User-Agent": "Selyrion/1.0 research-bot"},
        )
        data = r.json()

        results = []
        # Related topics as search results
        for item in data.get("RelatedTopics", [])[:limit]:
            if isinstance(item, dict) and item.get("FirstURL"):
                results.append({
                    "title":   item.get("Text", "")[:120],
                    "url":     item.get("FirstURL", ""),
                    "snippet": item.get("Text", "")[:300],
                })

        # Fallback: HTML scrape of DuckDuckGo results
        if not results:
            r2 = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            titles   = re.findall(r'class="result__a"[^>]*>([^<]+)</a>', r2.text)
            urls     = re.findall(r'class="result__url"[^>]*>([^<]+)</a>', r2.text)
            snippets = re.findall(r'class="result__snippet"[^>]*>([^<]+)</a>', r2.text)
            for i in range(min(limit, len(titles))):
                results.append({
                    "title":   titles[i].strip() if i < len(titles) else "",
                    "url":     urls[i].strip()   if i < len(urls)    else "",
                    "snippet": snippets[i].strip()if i < len(snippets)else "",
                })

        return {
            "status":  "success",
            "query":   query,
            "count":   len(results),
            "results": results,
            "abstract": data.get("Abstract", "")[:500] if data.get("Abstract") else "",
        }
    except Exception as e:
        return {"status": "error", "query": query, "error": str(e)}
