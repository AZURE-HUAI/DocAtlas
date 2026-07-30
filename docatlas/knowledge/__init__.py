"""Knowledge packs: each module understands one technical domain's vocabulary.

How this differs from a source adapter: an adapter understands a **site** (how
URLs are formed, how responses parse), while a knowledge pack understands a
**domain** (which prefixes are decoration rather than part of a name, and when
two differently-named APIs are one thing seen from two sides).

These rules are deliberately not configuration. Rules of the form "strip this
leading prefix", "these single letters are type prefixes" and "these two APIs
correspond via that metadata field" would turn into an unreadable, untestable
mini-language if expressed as config; as ordinary Python functions they stay
clear and can be unit tested directly.

Knowledge packs are optional: crawling and searching work without one, just with
fewer extra clues. A pack provides exactly the capabilities it implements — there
is no need to add empty methods to satisfy an interface.
"""
