"""Cluster diagnostics — quality report as scannable Markdown.

Produces a Markdown report at ``/data/clusters/{topic_slug}/{region}/diagnostics.md``
that surfaces:
- Cluster counts, sizes (histogram), noise ratio
- Top 10 keywords per cluster
- 3 representative post snippets per cluster (truncated to 200 chars)
- Source diversity warning if any cluster >80% from one source
- Language diversity per cluster
- Suggested parameter adjustments
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from src.schemas.cluster import Cluster, ClusteringResult

_log = structlog.get_logger(__name__)

SNIPPET_CHARS = 200
SOURCE_DOMINANCE_THRESHOLD = 0.80  # warn if one source > 80%


def generate_report(
    result: ClusteringResult,
    post_texts: dict[str, str],  # post_id → text
    output_path: Path,
    params: dict[str, Any] | None = None,
) -> str:
    """Generate and write a Markdown diagnostics report.

    Returns the report content.
    """
    lines: list[str] = []
    _h1(lines, f"Cluster Diagnostics — {result.topic} ({result.region})")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Total posts: {result.total_posts}")
    lines.append(f"Clusters found: {len(result.clusters)}")
    noise_pct = round(result.noise_count / result.total_posts * 100, 1) if result.total_posts else 0
    lines.append(f"Noise (unclustered): {result.noise_count} ({noise_pct}%)")
    lines.append("")

    # 1. Cluster size histogram
    _h2(lines, "1. Cluster Sizes")
    if result.clusters:
        sizes = sorted([c.size for c in result.clusters], reverse=True)
        lines.append(f"Range: {min(sizes)} – {max(sizes)}")
        # ASCII histogram
        max_bar = 40
        scale = max_bar / max(sizes) if max(sizes) > 0 else 1
        lines.append("```")
        for s in sizes:
            bar = "█" * max(1, int(s * scale))
            lines.append(f"  {bar} {s}")
        lines.append("```")
    else:
        lines.append("No clusters found — all posts classified as noise.")
    lines.append("")

    # 2. Per-cluster details
    _h2(lines, "2. Cluster Details")
    for c in result.clusters:
        _h3(lines, f"Cluster {c.cluster_id} — size {c.size}")
        lines.append("")

        # Keywords
        if c.keyword_summary:
            kw_str = ", ".join(c.keyword_summary[:10])
            lines.append(f"**Keywords:** {kw_str}")
            lines.append("")

        # Representative snippets
        lines.append("**Representative posts:**")
        for i, pid in enumerate(c.representative_post_ids[:3], 1):
            text = post_texts.get(pid, "")
            snippet = text[:SNIPPET_CHARS].replace("\n", " ")
            if len(text) > SNIPPET_CHARS:
                snippet += "…"
            lines.append(f"{i}. `{pid}`: {snippet}")
        lines.append("")

        # Source distribution
        if c.source_distribution:
            total = sum(c.source_distribution.values())
            lines.append("**Source distribution:**")
            for src, count in sorted(c.source_distribution.items(), key=lambda x: -x[1]):
                pct = round(count / total * 100, 1) if total else 0
                lines.append(f"  - {src}: {count} ({pct}%)")
                if pct > SOURCE_DOMINANCE_THRESHOLD * 100:
                    lines.append(f"    ⚠ This cluster is >{int(SOURCE_DOMINANCE_THRESHOLD * 100)}% from one source — bias risk")
            lines.append("")

        # Language distribution
        if c.language_distribution:
            lines.append("**Language distribution:**")
            for lang, count in sorted(c.language_distribution.items(), key=lambda x: -x[1]):
                lines.append(f"  - {lang}: {count}")
            lines.append("")

    # 3. Parameter suggestions
    _h2(lines, "3. Parameter Suggestions")
    if noise_pct > 40:
        lines.append(f"⚠ Noise ratio is high ({noise_pct}%). Consider:")
        lines.append("  - Lowering `hdbscan_min_cluster_size` (current: {})".format(
            params.get("hdbscan", {}).get("min_cluster_size", "N/A") if params else "N/A"
        ))
        lines.append("  - Lowering outlier_threshold (current: {})".format(
            params.get("outlier_threshold", "N/A") if params else "N/A"
        ))
    elif noise_pct < 5:
        lines.append(f"ℹ Noise ratio is low ({noise_pct}%). Consider:")
        lines.append("  - Raising `hdbscan_min_cluster_size` to produce tighter clusters")
        lines.append("  - Raising outlier_threshold to be more selective")
    else:
        lines.append(f"✓ Noise ratio ({noise_pct}%) is in a healthy range (5–40%).")
    lines.append("")

    # Build report
    report = "\n".join(lines)

    # Write to file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    _log.info("diag.report_written", path=str(output_path))

    return report


def compute_ctfidf_keywords(
    clusters: list[Cluster],
    post_texts: dict[str, str],
    top_n: int = 10,
    *,
    tokenizer: "object | None" = None,
) -> dict[str, list[str]]:
    """Compute class-based TF-IDF keywords for each cluster.

    Returns ``{cluster_id: [keyword, ...]}``.

    Implementation:  c-TF-IDF treats each cluster as a "document" by
    concatenating all its post texts, then computes TF-IDF weights where
    the "class" is the cluster.  This highlights words that are distinctive
    to each cluster vs. all others — much better than raw TF-IDF for
    understanding what makes a cluster unique.

    When *tokenizer* is provided (a ``Tokenizer`` from ``src.lang``), text is
    pre-tokenized before TfidfVectorizer, giving region-appropriate keyword
    extraction for CJK languages.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer

    def _prepare(text: str) -> str:
        if tokenizer is not None:
            return " ".join(tokenizer.tokenize(text))
        return text

    # Build cluster "documents"
    cluster_docs: dict[str, str] = {}
    for c in clusters:
        texts = [_prepare(post_texts.get(pid, "")) for pid in c.post_ids]
        cluster_docs[c.cluster_id] = "\n".join(texts)

    # Build a combined document set — one doc per cluster
    doc_ids = list(cluster_docs.keys())
    documents = [cluster_docs[cid] for cid in doc_ids]

    if len(documents) < 2:
        # Can't compute c-TF-IDF with < 2 clusters
        return {cid: [] for cid in doc_ids}

    # c-TF-IDF: TF-IDF across cluster documents
    if tokenizer is not None:
        vectorizer = TfidfVectorizer(
            max_features=1000,
            tokenizer=lambda x: x.split(),
            lowercase=False,
            ngram_range=(1, 2),
        )
    else:
        vectorizer = TfidfVectorizer(
            max_features=1000,
            stop_words="english",
            ngram_range=(1, 2),
        )
    tfidf_matrix = vectorizer.fit_transform(documents)
    feature_names = vectorizer.get_feature_names_out()

    keywords: dict[str, list[str]] = {}
    for i, cid in enumerate(doc_ids):
        row = tfidf_matrix[i].toarray().flatten()
        top_indices = row.argsort()[-top_n:][::-1]
        keywords[cid] = [feature_names[j] for j in top_indices]

    return keywords


# ---------------------------------------------------------------------------
# Report formatting helpers
# ---------------------------------------------------------------------------


def _h1(lines: list[str], text: str) -> None:
    lines.append(f"# {text}")
    lines.append("")


def _h2(lines: list[str], text: str) -> None:
    lines.append(f"## {text}")
    lines.append("")


def _h3(lines: list[str], text: str) -> None:
    lines.append(f"### {text}")
    lines.append("")
