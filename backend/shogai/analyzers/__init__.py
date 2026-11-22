"""The four Shōgai data-source processors, all powered by Gemini.

image    -> analyze_image     (photos, screenshots)
text     -> analyze_text      (personal thoughts, notes, chat exports, documents, web pages)
video    -> analyze_media     (video files, YouTube links; audio recordings use the same path)
academic -> analyze_academic  (papers: extraction + Google-Search-grounded fact-check)
"""

from .academic import analyze_academic
from .base import Analyzer, AnalysisResult, Item, StatusFn, SubjectContext, render_source_markdown
from .image import analyze_image
from .text import analyze_text
from .video import analyze_media

ANALYZERS: dict[str, Analyzer] = {
    "image": analyze_image,
    "text": analyze_text,
    "video": analyze_media,
    "audio": analyze_media,
    "academic": analyze_academic,
}

__all__ = [
    "ANALYZERS",
    "Analyzer",
    "AnalysisResult",
    "Item",
    "StatusFn",
    "SubjectContext",
    "analyze_academic",
    "analyze_image",
    "analyze_media",
    "analyze_text",
    "render_source_markdown",
]
