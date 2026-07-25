"""来源适配器：每个模块懂一个文档站点。

一个适配器要回答四件事：

    1. 这个站点有哪些页面？          list_sitemaps / categorize_sitemap /
                                     normalize_location
    2. 某一页的内容去哪里要？        document_request_url
    3. 拿回来的东西怎么解析成文档？  parse_document
    4. 某一页的正式地址长什么样？    canonical_url

其余的（限速、重试、落库、切块、检索）都是核心的事，适配器不用管。
照着 epic_ue.py 抄一份改掉这四件事，就能接一个新站点。
"""
