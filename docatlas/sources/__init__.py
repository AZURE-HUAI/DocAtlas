"""Source adapters: each module understands one documentation site.

An adapter answers four questions:

    1. Which pages does this site have?   list_sitemaps / categorize_sitemap /
                                          normalize_location
    2. Where is one page's content?       document_request_url
    3. How is the response parsed?        parse_document
    4. What is a page's canonical URL?    canonical_url

Everything else — rate limiting, retries, storage, chunking, retrieval — belongs
to the core and is not the adapter's concern. To support a new site, copy
`example.py` and replace those four answers; it is a working adapter for an
invented site, annotated throughout, and the test suite runs against it.

`_html.py` holds helpers for adapters that parse HTML rather than an API.
"""
