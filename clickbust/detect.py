#!/usr/bin/env python3
"""
detect.py — Image-subject detection module.

Detects whether an article's og:image matches the actual show/topic.

Three complementary signals:
  1. Article URL slug ↔ Image URL keyword overlap
  2. Alt text named entities ↔ Article URL slug match
  3. Image URL keywords ↔ Alt text mention

If ANY positive signal found → MATCH (high confidence → 0.85+)
If NO positive signal but alt text describes a specific entity → MISMATCH
Default fallback → MATCH (low confidence)

Usage:
  uv run python3 detect.py --csv <path> [--limit N]
  uv run python3 detect.py --eval <ground_truth.json>
"""

import csv
import json
import logging
import re
import string
import sys
from typing import Optional
from urllib.parse import urlparse, unquote

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ──
MEETS_RE = re.compile(r'\b(\w[\w\'.&-]*)\s+[Mm]eets\s+(\w[\w\'.&-]*)\b')

STOP_WORDS = {
    'the', 'and', 'for', 'what', 'when', 'where', 'that', 'this',
    'with', 'from', 'how', 'why', 'who', 'which', 'but', 'not',
    'are', 'was', 'has', 'had', 'its', 'all', 'you', 'your',
    'his', 'her', 'our', 'their', 'they', 'will', 'can', 'get',
    'got', 'been', 'being', 'one', 'two', 'new', 'more', 'some',
    'than', 'then', 'also', 'just', 'like', 'into', 'after',
    'over', 'under', 'very', 'much', 'many', 'each', 'every',
    'here', 'there', 'even', 'still', 'back', 'down', 'off', 'out',
    'way', 'day', 'man', 'top', 'best', 'big', 'old',
}

URL_NOISE = {
    'the','and','for','with','from','this','that','its','all','new','one','two',
    'in','of','on','at','by','to','is','it','meets','meet','not','are','was','has',
    '2016','2020','2024','2025','2026','v2','v3',
    'game','free','best','big','top','art','image','photo','picture','screen','press',
    'looking','standing','sitting','walking','running','flying',
    'character','scene','background','view','cover','poster','key','still','shot',
    'head','face','hand','back','side','jpg','jpeg','png','gif','webp',
    'movie','film','show','series','trailer','review','article','episode',
}


def extract_subjects(headline: str) -> tuple[Optional[str], Optional[str]]:
    """Extract 'X' and 'Y' from an 'X meets Y' headline."""
    m = MEETS_RE.search(headline)
    if not m:
        return None, None
    x = m.group(1).strip(string.punctuation + "'\" ").strip()
    y = m.group(2).strip(string.punctuation + "'\"():;,!?").strip()
    if x.lower() in STOP_WORDS or y.lower() in STOP_WORDS:
        return None, None
    return x, y


def slug_keywords(url: str) -> list[str]:
    """Extract meaningful keywords from a URL path slug."""
    if not url:
        return []
    path = unquote(urlparse(url).path).rstrip('/')
    slug = path.split('/')[-1] if '/' in path else path
    parts = re.split(r'[-_]+', slug.lower())
    return [p for p in parts if len(p) > 2 and p not in URL_NOISE]


def image_url_keywords(image_url: str) -> list[str]:
    """Extract meaningful keywords from an image URL filename."""
    if not image_url:
        return []
    path = unquote(urlparse(image_url).path)
    filename = path.rstrip('/').split('/')[-1] if '/' in path else path
    name = re.sub(r'\.(jpg|jpeg|png|gif|webp|avif)(\?.*)?$', '', filename, flags=re.I)
    parts = re.split(r'[-_]+', name)
    return [p.lower() for p in parts if len(p) > 2 and p.lower() not in URL_NOISE]


def extract_named_entities(text: str) -> list[str]:
    """Extract capitalized multi-word phrases (probable named entities)."""
    words = text.split()
    entities = []
    current = []
    for w in words:
        stripped = w.strip(string.punctuation)
        if stripped and stripped[0].isupper() and len(stripped) > 1:
            current.append(stripped)
        else:
            if len(current) >= 2:
                entities.append(' '.join(current))
            elif len(current) == 1 and len(current[0]) > 3:
                entities.append(current[0])
            current = []
    if len(current) >= 2:
        entities.append(' '.join(current))
    elif len(current) == 1 and len(current[0]) > 3:
        entities.append(current[0])
    return entities


def alt_is_descriptive(alt: str) -> bool:
    """Check if alt text describes a specific named entity."""
    if not alt.strip() or len(alt) < 10:
        return False
    words = alt.split()
    entities = extract_named_entities(alt)
    # A multi-word named entity is descriptive even with just 2 words
    if any(len(e.split()) >= 2 for e in entities):
        return True
    # More than 1 single-word entity is also descriptive
    if len(entities) >= 2:
        return True
    # At least 3 words needed for single entities
    if len(words) <= 2:
        return False
    return False


# ═══════════════════════════════════════════════════════════════════
# Main prediction function
# ═══════════════════════════════════════════════════════════════════
def predict(headline: str, image_url: str = '',
            alt_text_og: str = '', alt_text_body: str = '',
            article_snippet: str = '',
            article_url: str = '',
            **kwargs) -> tuple[str, float]:
    """Predict whether image matches article topic.

    Args:
        headline: Article headline (e.g., "Zelda meets Animal Crossing...")
        image_url: The og:image URL
        alt_text_og: og:image alt text from meta tag
        alt_text_body: First body image alt text
        article_snippet: Text snippet from the article body
        article_url: Full article URL (used for slug analysis)

    Returns:
        (label, confidence): ('MATCH', float) or ('MISMATCH', float)
    """
    alt = (alt_text_og or alt_text_body or '').strip()
    context = (headline + ' ' + (article_snippet or '')).lower()

    # ═══ SIGNAL 1: Article URL slug ↔ Image URL keyword overlap ═══
    if article_url or kwargs.get('article_url'):
        a_url = article_url or kwargs.get('article_url', '')
        slug_kw = set(slug_keywords(a_url))
        img_kw = set(image_url_keywords(image_url))
        slug_img_overlap = slug_kw & img_kw

        if slug_img_overlap:
            score = len(slug_img_overlap) / max(len(slug_kw | img_kw), 1)
            if score >= 0.15:
                return 'MATCH', 0.85
            else:
                return 'MATCH', 0.75

    # ═══ SIGNAL 2: Alt text mentions headline subjects ═══
    x, y = extract_subjects(headline)
    if x and y and alt:
        x_norm = x.lower().strip(string.punctuation)
        y_norm = y.lower().strip(string.punctuation)
        alt_lower = alt.lower()
        if x_norm in alt_lower or any(w.strip(string.punctuation) in alt_lower
                                       for w in x_norm.split() if len(w) > 2):
            if y_norm in alt_lower or any(w.strip(string.punctuation) in alt_lower
                                           for w in y_norm.split() if len(w) > 2):
                return 'MATCH', 0.95
            return 'MATCH', 0.85

    # ═══ SIGNAL 3: Alt entity in article slug ═══
    if alt and article_url:
        slug_kw = set(slug_keywords(article_url))
        alt_entities = extract_named_entities(alt)
        for ae in alt_entities:
            for w in ae.lower().split():
                w_clean = w.strip(string.punctuation)
                if len(w_clean) > 2 and w_clean in slug_kw:
                    return 'MATCH', 0.80

    # ═══ SIGNAL 4: Image URL keyword in alt text (context-aware) ═══
    # Cross-reference: image URL keyword appears in its own alt text AND
    # also appears in article context (headline + snippet).
    # Keywords ≥ 4 chars use substring matching (catches concatenated game
    # names like "SoftwareHexborn"). Shorter keywords use word-boundary
    # matching to avoid false substrings (e.g. "out" in "outer").
    if alt and image_url:
        context = (headline + ' ' + (article_snippet or '')).lower()
        alt_lower = alt.lower()
        img_kw_list = image_url_keywords(image_url)
        for kw in img_kw_list:
            if kw in alt_lower:
                if len(kw) >= 4:
                    if kw in context:
                        return 'MATCH', 0.75
                else:
                    # Short keyword: word-boundary match only
                    padded_kw = ' ' + kw + ' '
                    if padded_kw in ' ' + context + ' ':
                        return 'MATCH', 0.75

    # ═══ SIGNAL 5: Article slug vs image URL — MISMATCH detection ═══
    # If the image URL has many specific keywords that don't appear in
    # the article URL slug, and the alt is descriptive, it's likely
    # a wrong image.
    if article_url and image_url:
        slug_kw = set(slug_keywords(article_url))
        img_kw_set = set(image_url_keywords(image_url))
        img_only = img_kw_set - slug_kw
        if len(img_only) >= 3 and len(slug_kw) >= 3:
            if alt_is_descriptive(alt):
                return 'MISMATCH', 0.65

    # ═══ MISMATCH (fallback a): descriptive alt with no MATCH signals ═══
    if alt_is_descriptive(alt):
        return 'MISMATCH', 0.60

    # ═══ MISMATCH (fallback b): lowercase alt with many specific words ═══
    # Catches cases like alt="steam free horror game terminal lucidity"
    # where the text is all-lowercase (no named entities) but clearly
    # describes a specific subject unrelated to the article.
    if alt and image_url:
        img_kw_list = image_url_keywords(image_url)
        if len(img_kw_list) >= 3:
            alt_words = {w.strip(string.punctuation).lower()
                         for w in alt.split() if len(w) > 3}
            if len(alt_words) >= 4:
                ctx_words = {w.strip(string.punctuation)
                             for w in context.split() if len(w) > 2}
                if not (alt_words & ctx_words):
                    return 'MISMATCH', 0.50

    # ═══ FALLBACK ═══
    return 'MATCH', 0.40


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════
def run_eval(csv_path: str, truth_path: str = ''):
    """Evaluate predictions against merged dataset."""
    with open(csv_path, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    ground_truth = {}
    if truth_path:
        with open(truth_path) as f:
            truth_data = json.load(f)
        for url, entry in truth_data.get('matches', {}).items():
            ground_truth[url] = entry['label']
        for url, entry in truth_data.get('mismatches', {}).items():
            ground_truth[url] = entry['label']

    results = []
    for r in rows:
        url = r.get('url', '')
        if ground_truth and url not in ground_truth:
            # Create synthetic if article_url pattern for mismatches
            continue
        gt = ground_truth.get(url, 'MATCH')

        pred, conf = predict(
            r.get('title', ''),
            image_url=r.get('og_image', ''),
            alt_text_og=r.get('alt_text_of_og', ''),
            alt_text_body=r.get('alt_text_of_body', ''),
            article_snippet=r.get('article_text_snippet', ''),
            article_url=url,
        )
        correct = pred == gt
        results.append((url, gt, pred, conf, correct))

    if not results:
        return

    correct_count = sum(1 for r in results if r[4])
    total = len(results)
    acc = correct_count / total * 100

    print(f"\n{'='*65}")
    print(f"  IMAGE-SUBJECT DETECTION — EVALUATION")
    print(f"{'='*65}")
    print(f"  Samples: {total}")
    print(f"  Correct: {correct_count}/{total} = {acc:.1f}%")

    tp = sum(1 for r in results if r[1]=='MATCH' and r[3]=='MATCH')
    tn = sum(1 for r in results if r[1]=='MISMATCH' and r[3]=='MISMATCH')
    fp = sum(1 for r in results if r[1]=='MISMATCH' and r[3]=='MATCH')
    fn = sum(1 for r in results if r[1]=='MATCH' and r[3]=='MISMATCH')

    prec = tp/(tp+fp) if (tp+fp) else 0
    rec = tp/(tp+fn) if (tp+fn) else 0
    f1 = 2*prec*rec/(prec+rec) if (prec+rec) else 0

    print(f"  Precision: {prec:.3f}")
    print(f"  Recall:    {rec:.3f}")
    print(f"  F1:        {f1:.3f}")
    print(f"  Confusion: TP={tp} TN={tn} FP={fp} FN={fn}")
    print(f"{'='*65}")

    # Show errors
    errors = [r for r in results if not r[4]]
    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for u, gt, pred, conf, _ in errors[:10]:
            row = next((r for r in rows if r['url'] == u.replace('#mismatch-test','')), None)
            title = (row.get('title','') if row else '')[:50]
            print(f"    GT={gt} Pred={pred} (c={conf:.2f}) | {title}")

    with open('eval_results.json', 'w') as f:
        json.dump({
            'accuracy': acc,
            'precision': prec,
            'recall': rec,
            'f1': f1,
            'tp': tp, 'tn': tn, 'fp': fp, 'fn': fn,
        }, f, indent=2)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--eval':
        csv_path = sys.argv[2] if len(sys.argv) > 2 else 'merged_dataset.csv'
        truth_path = sys.argv[3] if len(sys.argv) > 3 else 'ground_truth_v2.json'
        run_eval(csv_path, truth_path)
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == '--csv':
        csv_path = sys.argv[2]
        limit = int(sys.argv[4]) if len(sys.argv) > 3 and sys.argv[3] == '--limit' else None
        with open(csv_path, newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        for i, r in enumerate(rows):
            if limit and i >= limit:
                break
            pred, conf = predict(r.get('title',''), image_url=r.get('og_image',''),
                                 alt_text_og=r.get('alt_text_of_og',''),
                                 alt_text_body=r.get('alt_text_of_body',''),
                                 article_snippet=r.get('article_text_snippet',''),
                                 article_url=r.get('url',''))
            print(f"[{i+1:3d}] {pred:8s} (c={conf:.2f}) | {r.get('title','')[:50]}")
        sys.exit(0)

    print("Usage: uv run python3 detect.py --eval [csv] [truth.json]")
    print("   or: uv run python3 detect.py --csv <file.csv> [--limit N]")
