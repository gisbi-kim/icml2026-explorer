"""Build a self-contained ICML 2026 accepted-paper explorer.

The official ICML virtual site publishes a miniconf JSON snapshot for 2026
orals/posters plus a separate abstract map. This script fetches those files,
deduplicates oral/poster event rows into paper rows, adds lightweight derived
fields, and writes JSON plus static HTML.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "raw" / "icml2026"
OUT_DIR = ROOT / "output"
RAW_EVENTS = RAW_DIR / "icml-2026-orals-posters.json"
RAW_ABSTRACTS = RAW_DIR / "icml-2026-abstracts.json"
OUT_JSON = OUT_DIR / "icml2026_papers.json"
OUT_HTML = OUT_DIR / "icml2026_explorer.html"

OPENREVIEW_NOTES_API = "https://api2.openreview.net/notes"
ICML_EVENTS_URL = "https://icml.cc/static/virtual/data/icml-2026-orals-posters.json"
ICML_ABSTRACTS_URL = "https://icml.cc/static/virtual/data/icml-2026-abstracts.json"
VENUES = [
    ("oral", "ICML 2026 oral"),
    ("spotlight", "ICML 2026 spotlight"),
    ("regular", "ICML 2026 regular"),
]
DECISION_RANK = {"oral": 0, "spotlight": 1, "regular": 2}

TOPICS = [
    (
        "LLMs and Generative AI",
        [
            "language model",
            "llm",
            "large language",
            "foundation model",
            "generative",
            "diffusion",
            "text-to-image",
            "prompt",
            "alignment",
            "instruction",
            "rlhf",
            "transformer",
            "chat",
        ],
    ),
    (
        "Vision and Multimodal",
        [
            "vision",
            "image",
            "video",
            "multimodal",
            "visual",
            "segmentation",
            "detection",
            "3d",
            "point cloud",
            "rendering",
            "vqa",
        ],
    ),
    (
        "Reinforcement Learning and Agents",
        [
            "reinforcement learning",
            "policy",
            "agent",
            "bandit",
            "reward",
            "planning",
            "control",
            "markov",
            "offline rl",
            "imitation",
        ],
    ),
    (
        "Optimization and Training",
        [
            "optimization",
            "optimizer",
            "gradient",
            "training",
            "fine-tuning",
            "pretraining",
            "scaling",
            "convergence",
            "loss landscape",
            "learning rate",
        ],
    ),
    (
        "Theory and Foundations",
        [
            "theory",
            "generalization",
            "sample complexity",
            "bounds",
            "provable",
            "theorem",
            "statistical",
            "causal",
            "bayesian",
            "kernel",
            "probabilistic",
        ],
    ),
    (
        "Trust, Safety, and Robustness",
        [
            "robust",
            "safety",
            "adversarial",
            "privacy",
            "fairness",
            "bias",
            "uncertainty",
            "calibration",
            "out-of-distribution",
            "ood",
            "hallucination",
            "watermark",
        ],
    ),
    (
        "Graphs and Structured Data",
        [
            "graph",
            "gnn",
            "network",
            "relational",
            "knowledge graph",
            "molecule",
            "protein",
            "structure",
        ],
    ),
    (
        "Data, Evaluation, and Benchmarks",
        [
            "dataset",
            "benchmark",
            "evaluation",
            "data",
            "annotation",
            "label",
            "synthetic data",
            "retrieval",
            "ranking",
            "metric",
        ],
    ),
    (
        "Healthcare, Bio, and Science",
        [
            "medical",
            "health",
            "clinical",
            "biology",
            "bio",
            "protein",
            "molecule",
            "drug",
            "genomic",
            "physics",
            "science",
        ],
    ),
    (
        "Robotics and Embodied AI",
        [
            "robot",
            "robotic",
            "embodied",
            "manipulation",
            "navigation",
            "autonomous",
            "locomotion",
            "sim-to-real",
        ],
    ),
]

KEYWORD_PATTERNS = {
    "LLM": r"\bLLMs?\b|large language model",
    "Diffusion": r"\bdiffusion\b|score-based",
    "Transformer": r"\btransformers?\b|attention",
    "Agents": r"\bagents?\b",
    "RL": r"reinforcement learning|\bRL\b|policy gradient",
    "Optimization": r"optimizers?|optimization|gradient",
    "Theory": r"generalization|sample complexity|theorem|provable|bounds?",
    "Robustness": r"robust|adversarial|out-of-distribution|\bOOD\b",
    "Privacy": r"privacy|private|federated",
    "Fairness": r"fairness|bias",
    "Causality": r"causal|causality",
    "Graphs": r"\bgraphs?\b|\bGNNs?\b|graph neural",
    "Vision": r"vision|image|video|visual|segmentation|detection",
    "Multimodal": r"multimodal|vision-language|VLM",
    "Retrieval": r"retrieval|rag|nearest neighbor",
    "Benchmark": r"benchmark|evaluation|dataset",
    "Robotics": r"robot|robotic|embodied|manipulation|navigation",
    "Healthcare": r"medical|clinical|healthcare|biology|protein|molecule|drug",
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def note_value(content: dict, key: str, default=None):
    value = content.get(key, default)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def clean_text(value: str | None) -> str:
    value = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip().casefold()


def request_json(url: str, retries: int = 3) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "icml2026-explorer-builder"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network retry guard
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def fetch_openreview_notes(force: bool = False) -> list[dict]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if RAW_NOTES.exists() and not force:
        return json.loads(RAW_NOTES.read_text(encoding="utf-8"))

    all_notes: list[dict] = []
    seen: set[str] = set()
    for decision, venue in VENUES:
        offset = 0
        limit = 1000
        while True:
            params = {
                "content.venue": venue,
                "limit": str(limit),
                "offset": str(offset),
            }
            url = f"{OPENREVIEW_NOTES_API}?{urllib.parse.urlencode(params)}"
            payload = request_json(url)
            notes = payload.get("notes", [])
            if not notes:
                break
            for note in notes:
                note["_decision"] = decision
                if note.get("id") not in seen:
                    seen.add(note.get("id"))
                    all_notes.append(note)
            if len(notes) < limit:
                break
            offset += limit

    RAW_NOTES.write_text(json.dumps(all_notes, ensure_ascii=False, indent=2), encoding="utf-8")
    return all_notes


def download_file(url: str, path: Path, force: bool = False) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "icml2026-explorer-builder"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        path.write_bytes(resp.read())


def fetch_icml_virtual_data(force: bool = False) -> tuple[list[dict], dict]:
    download_file(ICML_EVENTS_URL, RAW_EVENTS, force=force)
    download_file(ICML_ABSTRACTS_URL, RAW_ABSTRACTS, force=force)
    events_payload = json.loads(RAW_EVENTS.read_text(encoding="utf-8"))
    events = events_payload.get("results", events_payload if isinstance(events_payload, list) else [])
    abstracts = json.loads(RAW_ABSTRACTS.read_text(encoding="utf-8"))
    return events, abstracts


def infer_topic(title: str, abstract: str) -> str:
    text = f"{title} {abstract}".casefold()
    best_topic = "General ML"
    best_score = 0
    for topic, terms in TOPICS:
        score = 0
        for term in terms:
            score += len(re.findall(re.escape(term.casefold()), text))
        if score > best_score:
            best_topic = topic
            best_score = score
    return best_topic


def infer_keywords(title: str, abstract: str) -> list[str]:
    text = f"{title} {abstract}"
    keywords: list[str] = []
    for label, pattern in KEYWORD_PATTERNS.items():
        if re.search(pattern, text, flags=re.I):
            keywords.append(label)
    return keywords[:8]


def normalize_notes(notes: list[dict]) -> list[dict]:
    papers: list[dict] = []
    for note in notes:
        content = note.get("content", {})
        title = clean_text(note_value(content, "title"))
        abstract = clean_text(note_value(content, "abstract"))
        authors = [clean_text(a) for a in note_value(content, "authors", []) if clean_text(a)]
        authorids = [clean_text(a) for a in note_value(content, "authorids", []) if clean_text(a)]
        venue = clean_text(note_value(content, "venue"))
        decision = note.get("_decision") or venue.replace("ICML 2026", "").strip().casefold() or "regular"
        html_url = clean_text(note_value(content, "html"))
        note_id = note.get("id", "")
        forum = note.get("forum") or note_id
        topic = infer_topic(title, abstract)
        keywords = infer_keywords(title, abstract)
        papers.append(
            {
                "id": note_id,
                "forum": forum,
                "number": note.get("number"),
                "title": title,
                "authors": authors,
                "authorids": authorids,
                "abstract": abstract,
                "decision": decision,
                "decision_label": decision.title(),
                "decision_rank": DECISION_RANK.get(decision, 9),
                "venue": venue,
                "topic": topic,
                "keywords": keywords,
                "author_count": len(authors),
                "openreview_url": f"https://openreview.net/forum?id={forum}",
                "pdf_url": f"https://openreview.net/pdf?id={forum}",
                "virtual_url": html_url,
                "paperhash": clean_text(note_value(content, "paperhash")),
                "published_date": ms_to_date(note.get("pdate")),
                "modified_date": ms_to_date(note.get("mdate")),
            }
        )

    papers.sort(key=lambda p: (p["decision_rank"], (p["title"] or "").casefold()))
    return papers


def normalize_decision(row: dict, oral_titles: set[str]) -> str:
    title_key = norm_title(row.get("name", ""))
    if title_key in oral_titles:
        return "oral"
    decision = clean_text(row.get("decision")).casefold()
    if "spotlight" in decision:
        return "spotlight"
    if "regular" in decision or "poster" in decision:
        return "regular"
    return "other"


def normalize_institution(inst: str) -> str:
    inst = clean_text(inst)
    aliases = {
        "Apple Inc.": "Apple",
        "Google Deepmind": "Google DeepMind",
        "Google Research": "Google",
        "Meta AI": "Meta",
        "Facebook AI Research": "Meta",
        "UC Berkeley": "University of California, Berkeley",
        "MIT": "Massachusetts Institute of Technology",
        "CMU": "Carnegie Mellon University",
        "ETH Zurich": "ETH Zurich",
    }
    return aliases.get(inst, inst)


def normalize_events(events: list[dict], abstracts: dict) -> list[dict]:
    oral_by_title = {
        norm_title(row.get("name", "")): row
        for row in events
        if row.get("eventtype") == "Oral"
    }
    poster_rows = [row for row in events if row.get("eventtype") == "Poster"]
    papers: list[dict] = []
    seen_titles: set[str] = set()
    for row in poster_rows:
        title = clean_text(row.get("name"))
        title_key = norm_title(title)
        if not title or title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        abstract = clean_text(abstracts.get(str(row.get("id")), ""))
        authors_raw = row.get("authors") or []
        authors = [clean_text(a.get("fullname")) for a in authors_raw if clean_text(a.get("fullname"))]
        institutions = []
        for author in authors_raw:
            inst = normalize_institution(author.get("institution", ""))
            if inst and inst not in institutions:
                institutions.append(inst)
        oral_row = oral_by_title.get(title_key)
        decision = normalize_decision(row, set(oral_by_title))
        official_topic = clean_text(row.get("topic"))
        topic = official_topic or infer_topic(title, abstract)
        official_keywords = [clean_text(k) for k in (row.get("keywords") or []) if clean_text(k)]
        inferred_keywords = infer_keywords(title, abstract)
        keywords = []
        for keyword in official_keywords + inferred_keywords:
            if keyword not in keywords:
                keywords.append(keyword)
        virtual_path = row.get("virtualsite_url") or ""
        virtual_url = urllib.parse.urljoin("https://icml.cc", virtual_path)
        oral_url = ""
        oral_session = ""
        oral_start = ""
        if oral_row:
            oral_url = urllib.parse.urljoin("https://icml.cc", oral_row.get("virtualsite_url") or "")
            oral_session = clean_text(oral_row.get("session"))
            oral_start = clean_text(oral_row.get("starttime"))
        paper_url = clean_text(row.get("paper_url"))
        papers.append(
            {
                "id": str(row.get("id")),
                "uid": clean_text(row.get("uid")),
                "number": row.get("id"),
                "title": title,
                "authors": authors,
                "institutions": institutions,
                "abstract": abstract,
                "decision": decision,
                "decision_label": decision.title() if decision != "other" else "Poster",
                "decision_rank": DECISION_RANK.get(decision, 9),
                "raw_decision": clean_text(row.get("decision")),
                "eventtype": clean_text(row.get("eventtype")),
                "topic": topic or "General ML",
                "official_topic": official_topic,
                "keywords": keywords[:10],
                "author_count": len(authors),
                "institution_count": len(institutions),
                "session": clean_text(row.get("session")),
                "oral_session": oral_session,
                "poster_start": clean_text(row.get("starttime")),
                "poster_end": clean_text(row.get("endtime")),
                "oral_start": oral_start,
                "room": clean_text(row.get("room_name")),
                "openreview_url": paper_url,
                "pdf_url": clean_text(row.get("paper_pdf_url")),
                "virtual_url": virtual_url,
                "oral_url": oral_url,
                "sourceurl": clean_text(row.get("sourceurl")),
            }
        )
    papers.sort(key=lambda p: (p["decision_rank"], (p["title"] or "").casefold()))
    return papers


def ms_to_date(value) -> str:
    if not value:
        return ""
    try:
        return dt.datetime.fromtimestamp(float(value) / 1000, tz=dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def summarize(papers: list[dict]) -> dict:
    decision_counts = Counter(p["decision"] for p in papers)
    topic_counts = Counter(p["topic"] for p in papers)
    keyword_counts = Counter(k for p in papers for k in p["keywords"])
    unique_authors = len({a for p in papers for a in p["authors"]})
    institution_counts = Counter(inst for p in papers for inst in p.get("institutions", []))
    session_counts = Counter(p.get("session") or "Unscheduled" for p in papers)
    author_hist = Counter(str(min(p["author_count"], 12)) if p["author_count"] < 12 else "12+" for p in papers)
    return {
        "conference": "ICML 2026",
        "generated": now_iso(),
        "n_papers": len(papers),
        "decisions": dict(decision_counts),
        "topics": dict(topic_counts),
        "keywords": dict(keyword_counts),
        "top_institutions": dict(institution_counts.most_common(40)),
        "sessions": dict(session_counts),
        "unique_authors": unique_authors,
        "unique_institutions": len(institution_counts),
        "avg_authors": round(sum(p["author_count"] for p in papers) / max(len(papers), 1), 2),
        "author_histogram": dict(author_hist),
        "sources": [
            ICML_EVENTS_URL,
            ICML_ABSTRACTS_URL,
            "https://openreview.net/group?id=ICML.cc/2026/Conference",
            "https://icml.cc/Conferences/2026",
        ],
    }


def write_json(summary: dict, papers: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps({"summary": summary, "papers": papers}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_html(summary: dict, papers: list[dict]) -> str:
    data_json = json.dumps({"summary": summary, "papers": papers}, ensure_ascii=False)
    escaped_data = data_json.replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ICML 2026 Paper Explorer</title>
<style>
:root {{
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #1d2433;
  --muted: #667085;
  --line: #d9dee8;
  --blue: #2563eb;
  --green: #0f9f6e;
  --amber: #b7791f;
  --red: #b42318;
  --ink: #111827;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--text);
}}
header {{
  background: #121826;
  color: white;
  padding: 28px 24px 20px;
  border-bottom: 4px solid #0f9f6e;
}}
.wrap {{ max-width: 1280px; margin: 0 auto; }}
h1 {{ margin: 0 0 8px; font-size: 34px; line-height: 1.1; letter-spacing: 0; }}
.subhead {{ margin: 0; color: #cbd5e1; font-size: 15px; }}
.source-row {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }}
.source-row a {{
  color: #dbeafe;
  border: 1px solid rgba(219, 234, 254, 0.35);
  border-radius: 6px;
  padding: 7px 10px;
  text-decoration: none;
  font-size: 13px;
}}
main {{ padding: 22px 24px 42px; }}
.metrics {{
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}}
.metric {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
}}
.metric .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
.metric .value {{ font-size: 26px; font-weight: 760; margin-top: 5px; }}
.grid {{
  display: grid;
  grid-template-columns: minmax(270px, 330px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}}
.panel {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
}}
.panel h2 {{ margin: 0 0 12px; font-size: 16px; }}
.filters {{ position: sticky; top: 12px; }}
label {{ display: block; color: var(--muted); font-size: 12px; margin: 12px 0 6px; }}
input[type="search"], select {{
  width: 100%;
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
  background: white;
  color: var(--text);
  font: inherit;
}}
.segmented {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
}}
.segmented button {{
  border: 0;
  border-right: 1px solid var(--line);
  background: white;
  min-height: 38px;
  color: var(--text);
  cursor: pointer;
  font-weight: 650;
}}
.segmented button:last-child {{ border-right: 0; }}
.segmented button.active {{ background: var(--blue); color: white; }}
.checkline {{ display: flex; align-items: center; gap: 8px; margin-top: 12px; color: var(--text); font-size: 14px; }}
.actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }}
button.action {{
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  min-height: 38px;
  cursor: pointer;
  font-weight: 650;
}}
button.action.primary {{ background: var(--ink); color: white; border-color: var(--ink); }}
button.action.compact {{ padding: 0 12px; min-width: 132px; }}
.chart-stack {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }}
.bar-row {{ display: grid; grid-template-columns: minmax(92px, 150px) minmax(0, 1fr) 54px; gap: 10px; align-items: center; margin: 8px 0; }}
.bar-label {{ font-size: 13px; overflow-wrap: anywhere; }}
.bar-track {{ background: #edf0f5; border-radius: 4px; height: 12px; overflow: hidden; }}
.bar-fill {{ height: 12px; background: var(--blue); border-radius: 4px; }}
.bar-fill.green {{ background: var(--green); }}
.bar-fill.amber {{ background: var(--amber); }}
.bar-fill.red {{ background: var(--red); }}
.bar-value {{ text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; font-size: 13px; }}
.chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.chip {{
  border: 1px solid var(--line);
  border-radius: 999px;
  background: #f8fafc;
  padding: 6px 9px;
  font-size: 13px;
  cursor: pointer;
}}
.chip strong {{ color: var(--blue); }}
.results-head {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}}
.results-head h2 {{ margin: 0; font-size: 17px; }}
.results-tools {{ display: flex; align-items: center; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }}
.count {{ color: var(--muted); font-size: 14px; }}
.pagination {{
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin: 10px 0;
}}
.page-btn {{
  border: 1px solid var(--line);
  border-radius: 6px;
  width: 38px;
  height: 34px;
  background: #fff;
  cursor: pointer;
  font-size: 16px;
  font-weight: 800;
}}
.page-btn:disabled {{ color: #a8b0bd; cursor: default; background: #f8fafc; }}
.page-label {{ color: var(--muted); font-size: 13px; min-width: 190px; text-align: center; }}
.paper-list {{ display: grid; gap: 10px; }}
.paper {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 13px 14px;
}}
.paper-top {{ display: flex; align-items: flex-start; gap: 10px; justify-content: space-between; }}
.paper h3 {{ margin: 0; font-size: 16px; line-height: 1.35; }}
.badge-row {{ display: flex; flex-wrap: wrap; gap: 7px; margin: 8px 0; }}
.badge {{
  border-radius: 999px;
  padding: 4px 8px;
  background: #eef2ff;
  color: #3730a3;
  font-size: 12px;
  font-weight: 700;
}}
.badge.spotlight {{ background: #ecfdf3; color: #067647; }}
.badge.oral {{ background: #fef3c7; color: #92400e; }}
.badge.regular {{ background: #e0f2fe; color: #075985; }}
.authors {{ color: var(--muted); font-size: 13px; line-height: 1.45; }}
.abstract {{ display: none; color: #3b4454; line-height: 1.55; margin: 10px 0 0; font-size: 14px; }}
.paper.open .abstract {{ display: block; }}
.links {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
.links a {{
  color: var(--blue);
  text-decoration: none;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 13px;
  background: #eff6ff;
}}
.toggle {{
  border: 1px solid var(--line);
  border-radius: 6px;
  min-width: 34px;
  height: 32px;
  background: white;
  cursor: pointer;
  font-size: 18px;
}}
.empty {{ padding: 28px; text-align: center; color: var(--muted); }}
footer {{ color: var(--muted); font-size: 12px; margin-top: 20px; }}
@media (max-width: 900px) {{
  .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .grid {{ grid-template-columns: 1fr; }}
  .filters {{ position: static; }}
  .chart-stack {{ grid-template-columns: 1fr; }}
}}
@media (max-width: 540px) {{
  header, main {{ padding-left: 14px; padding-right: 14px; }}
  h1 {{ font-size: 28px; }}
  .metrics {{ grid-template-columns: 1fr; }}
  .segmented {{ grid-template-columns: repeat(2, 1fr); }}
  .actions {{ grid-template-columns: 1fr; }}
  .results-head {{ align-items: flex-start; flex-direction: column; }}
  .results-tools, .pagination {{ justify-content: flex-start; width: 100%; }}
  .page-label {{ min-width: 0; flex: 1; }}
  .paper-top {{ gap: 6px; }}
}}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>ICML 2026 Paper Explorer</h1>
    <p class="subhead">Accepted papers from official ICML virtual data · Seoul, South Korea · July 6-11, 2026 · Generated {html.escape(summary["generated"])}</p>
    <div class="source-row">
      <a href="https://icml.cc/virtual/2026/papers.html">Official papers page</a>
      <a href="https://openreview.net/group?id=ICML.cc/2026/Conference">OpenReview venue</a>
      <a href="https://icml.cc/Conferences/2026">Official ICML page</a>
      <a href="icml2026_papers.json">Embedded JSON snapshot</a>
    </div>
  </div>
</header>
<main>
<div class="wrap">
  <section class="metrics" id="metrics"></section>
  <div class="grid">
    <aside class="panel filters">
      <h2>Filters</h2>
      <label for="q">Search</label>
      <input id="q" type="search" placeholder="title, author, abstract, keyword">
      <label>Decision</label>
      <div class="segmented" id="decisionSeg">
        <button data-decision="all" class="active">All</button>
        <button data-decision="oral">Oral</button>
        <button data-decision="spotlight">Spotlight</button>
        <button data-decision="regular">Regular</button>
      </div>
      <label for="topic">Topic</label>
      <select id="topic"></select>
      <label for="sort">Sort</label>
      <select id="sort">
        <option value="decision">Decision tier</option>
        <option value="title">Title</option>
        <option value="authors_desc">Author count high to low</option>
        <option value="number">OpenReview number</option>
      </select>
      <label class="checkline"><input id="hasVirtual" type="checkbox"> Has ICML virtual page</label>
      <div class="actions">
        <button class="action" id="resetBtn">Reset</button>
        <button class="action primary" id="downloadBtn">Download JSON</button>
      </div>
    </aside>
    <section>
      <div class="chart-stack">
        <div class="panel">
          <h2>Decision Tiers</h2>
          <div id="decisionChart"></div>
        </div>
        <div class="panel">
          <h2>Topics</h2>
          <div id="topicChart"></div>
        </div>
      </div>
      <div class="panel" style="margin-bottom:16px">
        <h2>Keyword Signals</h2>
        <div class="chips" id="keywordChips"></div>
      </div>
      <div class="panel" style="margin-bottom:16px">
        <h2>Authors Per Paper</h2>
        <div id="authorChart"></div>
      </div>
      <div class="results-head">
        <h2>Papers</h2>
        <div class="results-tools">
          <button class="action compact" id="abstractAllBtn" type="button" aria-pressed="false">Open Abstracts</button>
          <div class="count" id="resultCount"></div>
        </div>
      </div>
      <div id="pagerTop"></div>
      <div class="paper-list" id="papers"></div>
      <div id="pagerBottom"></div>
      <footer>
        Topic and keyword labels are lightweight title/abstract heuristics, not official ICML tracks.
      </footer>
    </section>
  </div>
</div>
</main>
<script id="paper-data" type="application/json">{escaped_data}</script>
<script>
const DATA = JSON.parse(document.getElementById('paper-data').textContent);
const PAGE_SIZE = 500;
const papers = DATA.papers.map(p => ({{
  ...p,
  searchText: [
    p.title,
    (p.authors || []).join(' '),
    (p.institutions || []).join(' '),
    p.abstract,
    p.topic,
    p.session,
    p.oral_session,
    (p.keywords || []).join(' '),
    p.id,
    String(p.number || '')
  ].join(' ').toLowerCase()
}}));
const state = {{
  q: '',
  decision: 'all',
  topic: 'all',
  sort: 'decision',
  hasVirtual: false,
  keyword: '',
  page: 1,
  abstractsOpen: false
}};
const els = {{
  metrics: document.getElementById('metrics'),
  q: document.getElementById('q'),
  decisionSeg: document.getElementById('decisionSeg'),
  topic: document.getElementById('topic'),
  sort: document.getElementById('sort'),
  hasVirtual: document.getElementById('hasVirtual'),
  resetBtn: document.getElementById('resetBtn'),
  downloadBtn: document.getElementById('downloadBtn'),
  decisionChart: document.getElementById('decisionChart'),
  topicChart: document.getElementById('topicChart'),
  keywordChips: document.getElementById('keywordChips'),
  authorChart: document.getElementById('authorChart'),
  papers: document.getElementById('papers'),
  resultCount: document.getElementById('resultCount'),
  abstractAllBtn: document.getElementById('abstractAllBtn'),
  pagerTop: document.getElementById('pagerTop'),
  pagerBottom: document.getElementById('pagerBottom')
}};

function fmt(n) {{ return Number(n || 0).toLocaleString(); }}
function esc(s) {{
  return String(s || '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}
function decisionLabel(d) {{
  return d === 'oral' ? 'Oral' : d === 'spotlight' ? 'Spotlight' : d === 'regular' ? 'Regular' : 'All';
}}
function initFromUrl() {{
  const params = new URLSearchParams(location.search);
  state.q = params.get('q') || '';
  state.decision = params.get('decision') || 'all';
  state.topic = params.get('topic') || 'all';
  state.sort = params.get('sort') || 'decision';
  state.keyword = params.get('keyword') || '';
  state.hasVirtual = params.get('virtual') === '1';
  state.page = Math.max(1, Number(params.get('page') || 1));
  state.abstractsOpen = params.get('abstracts') === 'open';
  els.q.value = state.q;
  els.sort.value = state.sort;
  els.hasVirtual.checked = state.hasVirtual;
}}
function updateUrl() {{
  const params = new URLSearchParams();
  if (state.q) params.set('q', state.q);
  if (state.decision !== 'all') params.set('decision', state.decision);
  if (state.topic !== 'all') params.set('topic', state.topic);
  if (state.sort !== 'decision') params.set('sort', state.sort);
  if (state.keyword) params.set('keyword', state.keyword);
  if (state.hasVirtual) params.set('virtual', '1');
  if (state.page > 1) params.set('page', String(state.page));
  if (state.abstractsOpen) params.set('abstracts', 'open');
  const query = params.toString();
  history.replaceState(null, '', query ? `?${{query}}` : location.pathname);
}}
function resetPage() {{ state.page = 1; }}
function populateStatic() {{
  const s = DATA.summary;
  els.metrics.innerHTML = [
    ['Accepted papers', fmt(s.n_papers)],
    ['Oral / Spotlight', `${{fmt(s.decisions.oral || 0)}} / ${{fmt(s.decisions.spotlight || 0)}}`],
    ['Regular papers', fmt(s.decisions.regular || 0)],
    ['Institutions', fmt(s.unique_institutions)]
  ].map(([label, value]) => `<div class="metric"><div class="label">${{label}}</div><div class="value">${{value}}</div></div>`).join('');
  const topics = ['all', ...Object.keys(s.topics).sort((a,b) => s.topics[b] - s.topics[a])];
  els.topic.innerHTML = topics.map(t => `<option value="${{esc(t)}}">${{t === 'all' ? 'All topics' : esc(t)}}</option>`).join('');
  els.topic.value = state.topic;
}}
function barChart(counts, opts = {{}}) {{
  const entries = Object.entries(counts).sort((a,b) => b[1] - a[1]).slice(0, opts.limit || 12);
  const max = Math.max(1, ...entries.map(x => x[1]));
  return entries.map(([label, value], idx) => {{
    const cls = opts.colors ? opts.colors(label, idx) : '';
    return `<div class="bar-row">
      <div class="bar-label">${{esc(label)}}</div>
      <div class="bar-track"><div class="bar-fill ${{cls}}" style="width:${{Math.max(2, value / max * 100)}}%"></div></div>
      <div class="bar-value">${{fmt(value)}}</div>
    </div>`;
  }}).join('');
}}
function renderCharts(filtered) {{
  const dc = {{}}, tc = {{}}, kc = {{}}, ac = {{}};
  filtered.forEach(p => {{
    dc[p.decision] = (dc[p.decision] || 0) + 1;
    tc[p.topic] = (tc[p.topic] || 0) + 1;
    (p.keywords || []).forEach(k => kc[k] = (kc[k] || 0) + 1);
    const key = p.author_count >= 12 ? '12+' : String(p.author_count);
    ac[key] = (ac[key] || 0) + 1;
  }});
  els.decisionChart.innerHTML = barChart(dc, {{
    colors: label => label === 'oral' ? 'amber' : label === 'spotlight' ? 'green' : ''
  }});
  els.topicChart.innerHTML = barChart(tc, {{limit: 10, colors: (_, i) => i % 3 === 1 ? 'green' : i % 3 === 2 ? 'amber' : ''}});
  const keyEntries = Object.entries(kc).sort((a,b) => b[1] - a[1]).slice(0, 24);
  els.keywordChips.innerHTML = keyEntries.map(([k, v]) =>
    `<button class="chip" data-keyword="${{esc(k)}}">${{esc(k)}} <strong>${{fmt(v)}}</strong></button>`
  ).join('');
  const orderedAuthor = Object.fromEntries(Object.entries(ac).sort((a,b) => {{
    const av = a[0] === '12+' ? 12 : Number(a[0]);
    const bv = b[0] === '12+' ? 12 : Number(b[0]);
    return av - bv;
  }}));
  els.authorChart.innerHTML = barChart(orderedAuthor, {{limit: 20, colors: (_, i) => i % 2 ? 'green' : ''}});
}}
function filteredPapers() {{
  const q = state.q.trim().toLowerCase();
  let out = papers.filter(p => {{
    if (state.decision !== 'all' && p.decision !== state.decision) return false;
    if (state.topic !== 'all' && p.topic !== state.topic) return false;
    if (state.keyword && !(p.keywords || []).includes(state.keyword)) return false;
    if (state.hasVirtual && !p.virtual_url) return false;
    if (q && !p.searchText.includes(q)) return false;
    return true;
  }});
  out.sort((a,b) => {{
    if (state.sort === 'title') return a.title.localeCompare(b.title);
    if (state.sort === 'authors_desc') return b.author_count - a.author_count || a.title.localeCompare(b.title);
    if (state.sort === 'number') return (a.number || 0) - (b.number || 0);
    return a.decision_rank - b.decision_rank || a.title.localeCompare(b.title);
  }});
  return out;
}}
function paperCard(p) {{
  const authors = (p.authors || []).slice(0, 16).join(', ') + ((p.authors || []).length > 16 ? `, +${{p.authors.length - 16}} more` : '');
  const badges = [
    `<span class="badge ${{p.decision}}">${{decisionLabel(p.decision)}}</span>`,
    `<span class="badge">${{esc(p.topic)}}</span>`,
    p.session ? `<span class="badge">${{esc(p.session)}}</span>` : '',
    ...(p.keywords || []).slice(0, 5).map(k => `<span class="badge">${{esc(k)}}</span>`)
  ].join('');
  const links = [
    p.openreview_url ? `<a href="${{esc(p.openreview_url)}}">OpenReview</a>` : '',
    p.pdf_url ? `<a href="${{esc(p.pdf_url)}}">PDF</a>` : '',
    p.virtual_url ? `<a href="${{esc(p.virtual_url)}}">Poster page</a>` : '',
    p.oral_url ? `<a href="${{esc(p.oral_url)}}">Oral page</a>` : ''
  ].filter(Boolean).join('');
  return `<article class="paper${{state.abstractsOpen ? ' open' : ''}}">
    <div class="paper-top">
      <div>
        <h3>${{esc(p.title)}}</h3>
        <div class="badge-row">${{badges}}</div>
        <div class="authors">${{esc(authors)}} · ${{esc((p.institutions || []).slice(0, 8).join(', '))}} · #${{esc(p.number || p.id)}}</div>
      </div>
      <button class="toggle" title="Toggle abstract" aria-label="Toggle abstract">${{state.abstractsOpen ? '-' : '+'}}</button>
    </div>
    <p class="abstract">${{esc(p.abstract)}}</p>
    <div class="links">${{links}}</div>
  </article>`;
}}
function paginationHtml(totalPages) {{
  if (totalPages <= 1) return '';
  const prev = Math.max(1, state.page - 1);
  const next = Math.min(totalPages, state.page + 1);
  return `<nav class="pagination" aria-label="Paper pages">
    <button class="page-btn" data-page="${{prev}}" ${{state.page === 1 ? 'disabled' : ''}} aria-label="Previous page" title="Previous page">&larr;</button>
    <div class="page-label">Page ${{fmt(state.page)}} / ${{fmt(totalPages)}} &middot; max ${{fmt(PAGE_SIZE)}} cards</div>
    <button class="page-btn" data-page="${{next}}" ${{state.page === totalPages ? 'disabled' : ''}} aria-label="Next page" title="Next page">&rarr;</button>
  </nav>`;
}}
function render() {{
  document.querySelectorAll('#decisionSeg button').forEach(btn => btn.classList.toggle('active', btn.dataset.decision === state.decision));
  els.topic.value = state.topic;
  els.abstractAllBtn.textContent = state.abstractsOpen ? 'Close Abstracts' : 'Open Abstracts';
  els.abstractAllBtn.setAttribute('aria-pressed', state.abstractsOpen ? 'true' : 'false');
  const out = filteredPapers();
  const totalPages = Math.max(1, Math.ceil(out.length / PAGE_SIZE));
  if (state.page > totalPages) state.page = totalPages;
  if (state.page < 1) state.page = 1;
  updateUrl();
  renderCharts(out);
  const startIndex = out.length ? (state.page - 1) * PAGE_SIZE : 0;
  const endIndex = Math.min(out.length, startIndex + PAGE_SIZE);
  const rangeLabel = out.length ? `${{fmt(startIndex + 1)}}-${{fmt(endIndex)}} of ${{fmt(out.length)}}` : `0 of ${{fmt(out.length)}}`;
  els.resultCount.textContent = `${{rangeLabel}} filtered · ${{fmt(papers.length)}} total`;
  const pager = paginationHtml(totalPages);
  els.pagerTop.innerHTML = pager;
  els.pagerBottom.innerHTML = pager;
  if (!out.length) {{
    els.papers.innerHTML = '<div class="panel empty">No papers match the current filters.</div>';
    return;
  }}
  els.papers.innerHTML = out.slice(startIndex, endIndex).map(paperCard).join('');
}}
els.q.addEventListener('input', e => {{ state.q = e.target.value; resetPage(); render(); }});
els.decisionSeg.addEventListener('click', e => {{
  const btn = e.target.closest('button[data-decision]');
  if (!btn) return;
  state.decision = btn.dataset.decision;
  resetPage();
  render();
}});
els.topic.addEventListener('change', e => {{ state.topic = e.target.value; resetPage(); render(); }});
els.sort.addEventListener('change', e => {{ state.sort = e.target.value; resetPage(); render(); }});
els.hasVirtual.addEventListener('change', e => {{ state.hasVirtual = e.target.checked; resetPage(); render(); }});
els.keywordChips.addEventListener('click', e => {{
  const chip = e.target.closest('button[data-keyword]');
  if (!chip) return;
  state.keyword = state.keyword === chip.dataset.keyword ? '' : chip.dataset.keyword;
  resetPage();
  render();
}});
els.resetBtn.addEventListener('click', () => {{
  state.q = '';
  state.decision = 'all';
  state.topic = 'all';
  state.sort = 'decision';
  state.hasVirtual = false;
  state.keyword = '';
  state.page = 1;
  state.abstractsOpen = false;
  els.q.value = '';
  els.sort.value = 'decision';
  els.hasVirtual.checked = false;
  render();
}});
function handlePagerClick(e) {{
  const btn = e.target.closest('button[data-page]');
  if (!btn || btn.disabled) return;
  state.page = Math.max(1, Number(btn.dataset.page || 1));
  render();
  document.querySelector('.results-head').scrollIntoView({{behavior: 'smooth', block: 'start'}});
}}
els.pagerTop.addEventListener('click', handlePagerClick);
els.pagerBottom.addEventListener('click', handlePagerClick);
els.abstractAllBtn.addEventListener('click', () => {{
  state.abstractsOpen = !state.abstractsOpen;
  render();
}});
els.downloadBtn.addEventListener('click', () => {{
  const payload = JSON.stringify({{summary: DATA.summary, papers: filteredPapers()}}, null, 2);
  const blob = new Blob([payload], {{type: 'application/json'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'icml2026_filtered_papers.json';
  a.click();
  URL.revokeObjectURL(url);
}});
els.papers.addEventListener('click', e => {{
  const btn = e.target.closest('.toggle');
  if (!btn) return;
  const card = btn.closest('.paper');
  card.classList.toggle('open');
  btn.textContent = card.classList.contains('open') ? '-' : '+';
}});
initFromUrl();
populateStatic();
render();
</script>
</body>
</html>
"""


def main() -> None:
    events, abstracts = fetch_icml_virtual_data(force=False)
    papers = normalize_events(events, abstracts)
    summary = summarize(papers)
    write_json(summary, papers)
    OUT_HTML.write_text(build_html(summary, papers), encoding="utf-8")
    print(f"Wrote {OUT_JSON} ({len(papers)} papers)")
    print(f"Wrote {OUT_HTML}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
