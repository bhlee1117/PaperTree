#!/usr/bin/env python3
"""
build_db.py v2 — Zotero library -> labelled, clustered papers.json for dendrite_atlas.html

  1. read     a Zotero export (CSV / CSL-JSON / BibTeX) as the seed list
  2. enrich   each item from OpenAlex by DOI (abstract, references, citations, author IDs)
  3. label    against a fixed taxonomy with the Claude API
  4. cluster  by position-weighted author similarity (average linkage) + detect lab lineage
  5. write    papers.json

Zotero: right-click the collection -> Export Collection -> CSV (or CSL JSON).

  export ANTHROPIC_API_KEY=sk-...
  python build_db.py --zotero dendrites.csv --out papers.json
  python build_db.py --zotero dendrites.csv --no-label     # metadata + clusters only

Deps: pip install requests
"""

import argparse, csv, json, math, os, re, sys, time, itertools, unicodedata
from collections import Counter, defaultdict
import requests

OPENALEX = "https://api.openalex.org/works"
S2 = "https://api.semanticscholar.org/graph/v1/paper"
ANTHROPIC = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"
MAILTO = os.environ.get("OPENALEX_MAILTO", "")


# ═════════════════════════════════════════════════════════════════════════════
# TAXONOMY
#
# Three design rules that matter more than the values themselves:
#
#   • ONE QUESTION PER AXIS. "review" does not belong in `prep` — a review of
#     in vivo work has both a preparation and a document type. Mixing them means
#     you can never ask "show me all in vivo work, primary and review".
#
#   • MULTI-LABEL AXES ARE SEPARATE. A paper can report NMDA spikes *and* Ca
#     spikes. Forcing one loses data; allowing many breaks the tree (a paper
#     would appear in two branches). So: `phenomenon_primary` (single, drives
#     the hierarchy) and `phenomena` (multi, drives filtering).
#
#   • EVERY AXIS NEEDS AN ESCAPE HATCH. `unclear` / `not applicable`. Forcing a
#     choice on a paper that genuinely doesn't fit is what poisons the database.
# ═════════════════════════════════════════════════════════════════════════════
TAXONOMY = {
    # Level 1. Without this, the ~43% of this library that is hippocampal coding
    # and circuit work all collapses into "not applicable" on the dendrite axes
    # and piles up as one dead branch. Scope first, then the dendrite axes only
    # mean anything within the branches where they apply.
    "scope": {
        "label": "Scope",
        "values": {
            "dendritic computation": "The claim is about how dendrites transform input to output.",
            "hippocampal coding & memory": "Place fields, replay, sequences, assemblies, engrams.",
            "circuit & behavior": "Population or circuit level, not centred on dendrites or on coding per se.",
            "synaptic physiology": "Synapse-level transmission or plasticity, not framed dendritically.",
            "methods & tools": "Indicator, instrument, or analysis method is the contribution.",
            "other": "None of the above.",
        },
    },
    "article_type": {
        "label": "Article type",
        "values": {
            "primary research": "Reports new experimental or simulation results.",
            "review / perspective": "Synthesises existing work; no substantial new data.",
            "methods / tool": "The contribution is an indicator, instrument, or analysis method.",
            "theory": "Analytical or normative theory, not a simulation of a specific dataset.",
        },
    },
    "prep": {
        "label": "Preparation",
        "values": {
            "acute slice": "Acute brain slice.",
            "organotypic slice": "Organotypic or cultured slice, kept days to weeks.",
            "anesthetized in vivo": "In vivo under anesthesia.",
            "awake in vivo": "In vivo in an awake animal, head-fixed or freely moving.",
            "computational / modeling": "No new recordings; compartmental model or simulation.",
            "human tissue": "Human surgical or post-mortem tissue.",
            "dissociated culture": "Dissociated neuronal culture.",
            "unclear": "Not determinable from the text given. Use this rather than guessing.",
        },
    },
    "view": {
        "label": "Conceptual perspective",
        "values": {
            "dendritic-local computation centric": (
                "The dendrite is treated as a semi-autonomous computational unit. The paper's "
                "claim would survive even if the soma never spiked: local regenerative events, "
                "branch-specific integration, compartmentalised plasticity."
            ),
            "bAP-centric": (
                "The backpropagating action potential is the organising signal — the dendrite is "
                "instructed or read out by somatic output. STDP framing, bAP as the associative "
                "signal, somato-dendritic coupling measurements."
            ),
            "synaptic plasticity centric": (
                "The explanandum is a change in synaptic weight; dendritic events appear as the "
                "induction mechanism rather than as the object of study."
            ),
            "somatic-output centric": (
                "Dendritic properties are studied for how they shape somatic firing rate, "
                "bursting, or gain. The output code is what the paper is about."
            ),
            "mixed": "Genuinely balances two or more of the above. Use this rather than forcing a side.",
            "not applicable": "The paper takes no position on this axis.",
        },
    },
    "phenomenon_primary": {
        "label": "Primary phenomenon",
        "values": {
            "NMDA spike": "NMDAR-dependent regenerative depolarisation in thin dendrites.",
            "Ca spike": "Dendritic calcium spike, typically at the apical initiation zone.",
            "Na spike": "Fast sodium-dependent dendritic spike.",
            "bAP": "Backpropagating action potential.",
            "plateau potential": "Long-lasting depolarising plateau, tens to hundreds of ms.",
            "passive integration": "The claim concerns subthreshold / linear integration or cable properties.",
            "branch-specific computation": "The unit of interest is the branch, without one spike type dominating.",
            "none / not applicable": "No single dendritic phenomenon is central.",
        },
    },
}

# Multi-label axis: filtering only, never a hierarchy level.
MULTI = {
    "phenomena": {
        "label": "All phenomena reported",
        "values": ["NMDA spike", "Ca spike", "Na spike", "bAP", "plateau potential",
                   "passive integration", "branch-specific computation"],
    }
}

SIG = ("One sentence, 15-30 words, in {lang}. Format: <what was shown>, which <what it changed>. "
       "Write the field-level consequence, not a list of results. "
       "Bad: 'The authors recorded dendritic calcium signals in layer 5 neurons.' "
       "Good: 'Showed distal input alone can drive somatic bursts via a dendritic Ca spike, "
       "establishing L5 pyramidal cells as two-compartment coincidence detectors.'")


# ═════════════════════════════════════════════════════════════════════════════
# 1. READ ZOTERO
# ═════════════════════════════════════════════════════════════════════════════
def match_key(p):
    """DOI when there is one, else a normalised title. Zotero items without a DOI
    are the ones that drift, so the title fallback is deliberately aggressive."""
    doi = (p.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    t = re.sub(r"[^a-z0-9]+", "", (p.get("title") or "").lower())
    return "ttl:" + t[:80]


def _split_names(field):
    """Zotero CSV packs authors as 'Stuart, Greg J.; Sakmann, Bert'."""
    out = []
    for chunk in re.split(r";\s*", field or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "," in chunk:
            last, first = chunk.split(",", 1)
            out.append(f"{first.strip()} {last.strip()}".strip())
        else:
            out.append(chunk)
    return out


def read_zotero_csv(path):
    items = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r.get("Item Type") or "").lower() in ("attachment", "note"):
                continue
            yr = (r.get("Publication Year") or "").strip()
            items.append({
                "title": (r.get("Title") or "").strip(),
                "year": int(yr) if yr.isdigit() else None,
                "doi": (r.get("DOI") or "").strip().replace("https://doi.org/", ""),
                "authors": _split_names(r.get("Author", "")),
                "venue": (r.get("Publication Title") or "").strip(),
                "abstract": (r.get("Abstract Note") or "").strip(),
                "zotero_tags": [t.strip() for t in re.split(r";\s*", r.get("Manual Tags", "") or "") if t.strip()],
                "zotero_extra": (r.get("Extra") or "").strip(),
            })
    return items


def read_csl_json(path):
    items = []
    for r in json.load(open(path, encoding="utf-8")):
        auth = [(" ".join(x for x in [a.get("given"), a.get("family")] if x).strip()
                 or a.get("literal", "")) for a in r.get("author", [])]
        dp = (r.get("issued") or {}).get("date-parts") or [[None]]
        kw = r.get("keyword", "")
        items.append({
            "title": r.get("title", ""),
            "year": dp[0][0] if dp and dp[0] else None,
            "doi": (r.get("DOI") or "").strip(),
            "authors": [a for a in auth if a],
            "venue": r.get("container-title", ""),
            "abstract": r.get("abstract", ""),
            "zotero_tags": [k.strip() for k in kw.split(",") if k.strip()] if isinstance(kw, str) else [],
            "zotero_extra": r.get("note", ""),
        })
    return items


def read_bibtex(path):
    """Deliberately minimal — CSV or CSL JSON is the better path."""
    txt = open(path, encoding="utf-8").read()
    items = []
    for entry in re.split(r"@\w+\s*\{", txt)[1:]:
        def fld(name):
            m = re.search(name + r"\s*=\s*[{\"](.+?)[}\"]\s*,?\s*\n", entry, re.S | re.I)
            return re.sub(r"[{}]", "", m.group(1)).strip() if m else ""
        if not fld("title"):
            continue
        yr = fld("year")
        auths = []
        for a in re.split(r"\s+and\s+", fld("author")):
            a = a.strip()
            if a:
                auths += _split_names(a) if "," in a else [a]
        items.append({
            "title": fld("title"), "year": int(yr) if yr.isdigit() else None,
            "doi": fld("doi").replace("https://doi.org/", ""),
            "authors": auths,
            "venue": fld("journal") or fld("booktitle"),
            "abstract": fld("abstract"), "zotero_tags": [], "zotero_extra": "",
        })
    return items


def dedupe(items):
    """Zotero libraries accumulate duplicates — the same DOI entered twice, or a
    preprint and its published version. Left alone, every rebuild treats the second
    copy as a new paper and pays to label it again. Keep the richest copy."""
    best = {}
    collisions = []
    for it in items:
        k = match_key(it)
        if k in best:
            collisions.append((it.get("title", "")[:60], k))
            a, b = best[k], it
            richer = max((a, b), key=lambda x: (len(x.get("abstract") or ""),
                                                x.get("year") or 0,
                                                len(x.get("authors") or [])))
            poorer = b if richer is a else a
            # do not lose tags you added to whichever copy you were looking at
            richer["zotero_tags"] = sorted(set(richer.get("zotero_tags", []))
                                           | set(poorer.get("zotero_tags", [])))
            best[k] = richer
        else:
            best[k] = it
    if collisions:
        print(f"  ! {len(collisions)} duplicates merged — clean these up in Zotero:")
        for t, _ in collisions[:10]:
            print(f"      {t}")
    return list(best.values())


def read_zotero(path):
    ext = os.path.splitext(path)[1].lower()
    items = (read_zotero_csv(path) if ext == ".csv" else
             read_csl_json(path) if ext == ".json" else
             read_bibtex(path))
    items = dedupe(items)
    print(f"  {len(items)} items  ({sum(1 for i in items if i['doi'])} with DOI, "
          f"{sum(1 for i in items if len(i['abstract']) > 120)} with a usable abstract)")
    return items


# ═════════════════════════════════════════════════════════════════════════════
# 2. ENRICH FROM OPENALEX
# ═════════════════════════════════════════════════════════════════════════════
def deinvert(inv):
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def _get(url, params, tries=4):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503):
                time.sleep(2 ** k); continue
            return None
        except requests.RequestException:
            time.sleep(2 ** k)
    return None


def enrich(items):
    """Zotero is the seed list; OpenAlex is the metadata source of truth.
    Zotero's own abstract is kept only as a fallback — it is often empty or
    truncated depending on how the item was imported."""
    dois = [i["doi"].lower() for i in items if i["doi"]]
    found = {}
    for k in range(0, len(dois), 40):
        params = {"filter": "doi:" + "|".join(dois[k:k + 40]), "per-page": 50}
        if MAILTO:
            params["mailto"] = MAILTO
        for w in (_get(OPENALEX, params) or {}).get("results", []):
            d = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            found[d] = w
        print(f"  enriched {len(found)}/{len(dois)}")
        time.sleep(0.3)
    if dois and not found:
        print("  ! OpenAlex returned nothing for any DOI — network blocked, or the API is down.\n"
              "    Falling back to Zotero's own fields. Author IDs, reference lists and citation\n"
              "    counts will be missing, so clustering will be weaker. Re-run later, or pass\n"
              "    --no-enrich to make this explicit.", file=sys.stderr)

    out = []
    for it in items:
        w = found.get(it["doi"].lower()) if it["doi"] else None
        p = dict(it)
        p["id"] = (it["doi"] or it["title"][:60]).replace("/", "_").replace(" ", "_")
        if w:
            auths = [a["author"]["display_name"] for a in w.get("authorships", []) if a.get("author")]
            ids = [a["author"].get("id", "").split("/")[-1] for a in w.get("authorships", []) if a.get("author")]
            p["authors"] = auths or p["authors"]
            p["author_ids"] = ids or p["authors"]
            p["abstract"] = deinvert(w.get("abstract_inverted_index")) or p["abstract"]
            p["year"] = w.get("publication_year") or p["year"]
            p["venue"] = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or p["venue"]
            p["cited_by"] = w.get("cited_by_count", 0)
            p["refs"] = [r.split("/")[-1] for r in w.get("referenced_works", [])]
        else:
            p["author_ids"] = p["authors"]
            p["cited_by"] = 0
            p["refs"] = []
            if it["doi"]:
                print(f"  ! no OpenAlex record: {it['doi']}", file=sys.stderr)
        p["first_author"] = p["authors"][0] if p["authors"] else ""
        p["last_author"] = p["authors"][-1] if p["authors"] else ""
        out.append(p)
    return out


def add_tldr(papers):
    ids = [f"DOI:{p['doi']}" for p in papers if p.get("doi")]
    by = {}
    for i in range(0, len(ids), 100):
        try:
            r = requests.post(f"{S2}/batch", params={"fields": "externalIds,tldr"},
                              json={"ids": ids[i:i + 100]}, timeout=40)
            if r.status_code == 200:
                for row in r.json():
                    if row and row.get("tldr") and row.get("externalIds", {}).get("DOI"):
                        by[row["externalIds"]["DOI"].lower()] = row["tldr"]["text"]
        except requests.RequestException:
            pass
        time.sleep(1)
    for p in papers:
        p["tldr"] = by.get(p.get("doi", "").lower(), "")
    return papers


# ═════════════════════════════════════════════════════════════════════════════
# 3. LABEL
# ═════════════════════════════════════════════════════════════════════════════
def build_prompt(p, lang):
    axes = {k: v["values"] for k, v in TAXONOMY.items()}
    return f"""Classify this neuroscience paper into a fixed taxonomy for a literature database.

SINGLE-CHOICE AXES (exactly one value each, from the listed values only):
{json.dumps(axes, indent=2)}

MULTI-CHOICE AXIS "phenomena" — list every dendritic phenomenon the paper actually
reports data on, from: {MULTI['phenomena']['values']}. An empty list is allowed.

PAPER
title: {p['title']}
year: {p.get('year')}
venue: {p.get('venue')}
authors: {', '.join(p.get('authors', [])[:10])}
abstract: {p.get('abstract', '')[:5000]}

Return ONLY a JSON object, no prose, no fences:
{{
  "labels": {{ "scope":"...", "article_type":"...", "prep":"...", "view":"...", "phenomenon_primary":"..." }},
  "phenomena": ["..."],
  "confidence": {{ "scope":0.0-1.0, "article_type":0.0-1.0, "prep":0.0-1.0, "view":0.0-1.0, "phenomenon_primary":0.0-1.0 }},
  "evidence": {{ "view":"<=12 words from the abstract justifying the view label",
                 "prep":"<=12 words justifying the preparation" }},
  "significance": "...",
  "keywords": ["3-6 short free-form tags"]
}}

significance: {SIG.format(lang=lang)}

RULES
- Judge only from the text above. If the abstract does not state the preparation,
  answer "unclear" — do NOT infer it from the authors or the journal.
- This library is not all about dendrites. If "scope" is not "dendritic computation",
  then "view" and "phenomenon_primary" are usually "not applicable" / "none". Say so
  rather than stretching a dendritic reading onto a paper that has none.
- "view" asks what the paper's framing is, not which techniques it used. A paper can
  measure bAPs while being dendritic-local centric, and the reverse. Prefer "mixed"
  over a forced choice.
- confidence below 0.6 means a human should check it. Be honest; an over-confident
  wrong label costs far more here than a flagged uncertain one."""


def call_claude(prompt, key):
    r = requests.post(ANTHROPIC,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json={"model": MODEL, "max_tokens": 900,
                            "messages": [{"role": "user", "content": prompt}]},
                      timeout=90)
    r.raise_for_status()
    txt = "".join(b.get("text", "") for b in r.json().get("content", []))
    return json.loads(re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip())


def label_papers(papers, lang="English"):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY not set (or pass --no-label)")
    valid = {ax: set(cfg["values"]) for ax, cfg in TAXONOMY.items()}
    ok_phen = set(MULTI["phenomena"]["values"])
    for i, p in enumerate(papers, 1):
        # No abstract -> do not guess. Labelling from the title alone is the single
        # biggest source of confident-but-wrong entries.
        if len(p.get("abstract", "")) < 120:
            p["labels"] = {"scope": "other", "article_type": "primary research", "prep": "unclear",
                           "view": "not applicable", "phenomenon_primary": "none / not applicable"}
            p["phenomena"] = []
            p["confidence"] = {ax: 0.0 for ax in valid}
            p["evidence"] = {}
            p["significance"] = p.get("tldr", "") or "(no abstract — label this one from the PDF)"
            p["needs_review"] = True
            print(f"  [{i}/{len(papers)}] SKIP no abstract · {p['title'][:48]}")
            continue
        try:
            out = call_claude(build_prompt(p, lang), key)
        except Exception as e:
            print(f"  ! {p['title'][:48]}: {e}", file=sys.stderr)
            p.setdefault("labels", {}); p.setdefault("confidence", {}); p.setdefault("phenomena", [])
            p["needs_review"] = True
            continue
        labels = out.get("labels", {})
        for ax, allowed in valid.items():             # reject anything off-enum
            if labels.get(ax) not in allowed:
                labels[ax] = list(allowed)[-1]
                out.setdefault("confidence", {})[ax] = 0.0
        p["labels"] = labels
        p["phenomena"] = [x for x in out.get("phenomena", []) if x in ok_phen]
        p["confidence"] = out.get("confidence", {})
        p["evidence"] = out.get("evidence", {})
        p["significance"] = out.get("significance", "")
        p["keywords"] = out.get("keywords", [])
        p["needs_review"] = min([float(x) for x in p["confidence"].values()] or [1]) < 0.6
        print(f"  [{i}/{len(papers)}] {p['title'][:56]}")
        time.sleep(0.2)
    return papers


# ═════════════════════════════════════════════════════════════════════════════
# 4. WEIGHTED AUTHOR SIMILARITY + CLUSTERING
# ═════════════════════════════════════════════════════════════════════════════
# Position weights. In this field the last author IS the lab, so it carries the
# most identity. The first author is the person who did the work — they carry
# technique and question with them when they move, so they matter almost as much.
# The second-to-last slot is where a collaborating PI usually sits. Middle authors
# are often courtesy or resource contributions and should not dominate.
W_LAST, W_FIRST, W_PENULT, W_SECOND, W_MID = 1.00, 0.85, 0.45, 0.35, 0.12


def author_weights(auth):
    n = len(auth)
    if n == 0:
        return {}
    if n == 1:
        return {auth[0]: 1.0}
    w = {}
    for i, a in enumerate(auth):
        if i == n - 1:   x = W_LAST
        elif i == 0:     x = W_FIRST
        elif i == n - 2: x = W_PENULT
        elif i == 1:     x = W_SECOND
        else:            x = W_MID
        w[a] = max(w.get(a, 0.0), x)   # same person twice -> keep the strongest slot
    return w


def wcosine(wa, wb):
    """Weighted cosine over author vectors. Cosine rather than weighted Jaccard so a
    3-author paper is not penalised for matching a 25-author consortium paper."""
    if not wa or not wb:
        return 0.0
    dot = sum(v * wb[k] for k, v in wa.items() if k in wb)
    if dot == 0:
        return 0.0
    na = math.sqrt(sum(v * v for v in wa.values()))
    nb = math.sqrt(sum(v * v for v in wb.values()))
    return dot / (na * nb)


def average_linkage(sim, n, tau):
    """Agglomerative average linkage. NOT union-find: union-find is single linkage,
    which chains — A~B and B~C merge A with C even when A and C share nobody. That is
    exactly what glues an entire subfield into one useless blob."""
    dist = [[1.0 - sim[i][j] for j in range(n)] for i in range(n)]
    clusters = {i: [i] for i in range(n)}
    thresh = 1.0 - tau
    while len(clusters) > 1:
        keys = list(clusters)
        best = bi = bj = None
        for a, b in itertools.combinations(keys, 2):
            if best is None or dist[a][b] < best:
                best, bi, bj = dist[a][b], a, b
        if best is None or best >= thresh:
            break
        ni, nj = len(clusters[bi]), len(clusters[bj])
        for k in clusters:                       # Lance-Williams update
            if k in (bi, bj):
                continue
            d = (ni * dist[bi][k] + nj * dist[bj][k]) / (ni + nj)
            dist[bi][k] = dist[k][bi] = d
        clusters[bi] += clusters[bj]
        del clusters[bj]
    return list(clusters.values())


def surname(name):
    """Fold accents and case. Your export has both 'Buzsaki' and 'Buzsáki' as last
    authors — without this they become two different labs. OpenAlex author IDs fix
    this for items with a DOI; this catches the rest."""
    if not name:
        return ""
    last = name.split()[-1]
    return "".join(c for c in unicodedata.normalize("NFKD", last)
                   if not unicodedata.combining(c)).lower()


def display_surname(name):
    return name.split()[-1] if name else ""


def cluster_authors(papers, tau, topk=4):
    n = len(papers)
    keys = [p.get("author_ids") or p.get("authors", []) for p in papers]
    weights = [author_weights(k) for k in keys]

    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        sim[i][i] = 1.0
    for i, j in itertools.combinations(range(n), 2):
        s = wcosine(weights[i], weights[j])
        # Same last author = same lab. Floor the similarity so a PI's two very
        # different papers still land together even with no other shared authors.
        li, lj = surname(papers[i].get("last_author", "")), surname(papers[j].get("last_author", ""))
        if li and li == lj:
            s = max(s, 0.75)
        sim[i][j] = sim[j][i] = s

    for cid, members in enumerate(sorted(average_linkage(sim, n, tau), key=len, reverse=True)):
        lasts = Counter()
        pretty = {}
        for i in members:
            la = papers[i].get("last_author")
            if la:
                k = surname(la)
                lasts[k] += 1
                pretty.setdefault(k, display_surname(la))
        top = [pretty[x] for x, _ in lasts.most_common(2)]
        name = " + ".join(top) if top else "unassigned"
        if len(members) == 1:
            name += " (single)"
        for i in members:
            papers[i]["lab_cluster"] = cid
            papers[i]["lab_cluster_name"] = name

    for i, p in enumerate(papers):
        near = sorted(((sim[i][j], j) for j in range(n) if j != i), reverse=True)[:topk]
        p["similar"] = [{"id": papers[j]["id"], "score": round(s, 3),
                         "title": papers[j]["title"], "year": papers[j].get("year")}
                        for s, j in near if s > 0.05]
    return papers


def find_lineage(papers):
    """Someone who is first author on one paper and last author on a later one has
    almost certainly started their own lab. Author overlap will never link those two
    groups, but intellectually they are one lineage — which is usually what
    'papers from a similar group' actually means."""
    first_of, last_of = defaultdict(list), defaultdict(list)
    for i, p in enumerate(papers):
        ids = p.get("author_ids") or p.get("authors", [])
        if len(ids) >= 2:
            first_of[ids[0]].append(i)
            last_of[ids[-1]].append(i)
    links = []
    for person in set(first_of) & set(last_of):
        ty = [papers[i].get("year") or 0 for i in first_of[person]]
        py = [papers[i].get("year") or 0 for i in last_of[person]]
        if not ty or not py or min(py) <= min(ty):
            continue                    # not a trainee -> PI transition within this library
        name = next((papers[i]["authors"][0] for i in first_of[person]
                     if papers[i].get("authors")), str(person))
        parents = {papers[i]["lab_cluster_name"] for i in first_of[person]}
        own = {papers[i]["lab_cluster_name"] for i in last_of[person]}
        if parents - own:
            links.append({"person": name, "trained_in": sorted(parents - own),
                          "now_leads": sorted(own),
                          "as_first": len(first_of[person]), "as_last": len(last_of[person])})
    for p in papers:
        p["lineage"] = [l["person"] for l in links
                        if p["lab_cluster_name"] in l["now_leads"] + l["trained_in"]]
    return links


# ═════════════════════════════════════════════════════════════════════════════
# 5. INCREMENTAL UPDATE
#
# The point of --merge: a rebuild must never re-spend money on papers that are
# already labelled, and must never silently overwrite a label you fixed by hand.
# Everything the viewer writes (edited labels, human_verified, note) is carried
# forward verbatim. Only genuinely new papers hit the API.
#
# Clustering is the exception — it is global by nature, so it is always recomputed
# from scratch. Cluster *numbers* therefore change between runs; cluster *names*
# (the last-author surname) are what stay meaningful. Do not build anything that
# depends on lab_cluster being a stable integer.
# ═════════════════════════════════════════════════════════════════════════════
# What survives a rebuild. `note` is yours, written in the viewer; `zotero_extra`
# comes from Zotero's Extra field and is refreshed from the export every time.
CARRY = ("labels", "phenomena", "confidence", "evidence", "significance",
         "keywords", "needs_review", "human_verified", "note", "tldr")


def merge_existing(papers, path, force_relabel=()):
    """Returns (papers, stats). Papers that already carry labels get them back."""
    if not os.path.exists(path):
        print(f"  no existing {path} — full build")
        return papers, {"new": len(papers), "cached": 0, "dropped": 0, "repaired": 0}
    old = {match_key(p): p for p in json.load(open(path))["papers"]}
    new = cached = repaired = 0
    for p in papers:
        prev = old.pop(match_key(p), None)
        if not prev or not prev.get("labels"):
            new += 1
            continue
        # A previously-failed paper that now has an abstract should be retried.
        if prev.get("needs_review") and not prev.get("human_verified") \
                and len(p.get("abstract", "")) >= 120 \
                and not (prev.get("confidence") or {}):
            repaired += 1
            continue
        for k in CARRY:
            if k in prev:
                p[k] = prev[k]
        # A hand-fixed label always wins, even over a forced relabel.
        if force_relabel and not prev.get("human_verified"):
            for ax in force_relabel:
                p.get("labels", {}).pop(ax, None)
                p.get("confidence", {}).pop(ax, None)
            new += 1
            continue
        cached += 1
    print(f"  {cached} cached · {new} new · {repaired} retried · {len(old)} gone from Zotero")
    for p in list(old.values())[:5]:
        print(f"    - dropped: {(p.get('title') or '')[:60]}")
    return papers, {"new": new, "cached": cached, "dropped": len(old), "repaired": repaired}


def needs_call(p):
    return not p.get("labels") or not p.get("confidence")


def estimate(papers):
    todo = [p for p in papers if needs_call(p) and len(p.get("abstract", "")) >= 120]
    tok = sum(1200 + min(len(p.get("abstract", "")), 5000) // 4 for p in todo)
    print(f"\n  would label {len(todo)} papers · ~{tok:,} input tokens "
          f"· roughly ${tok/1e6*3 + len(todo)*400/1e6*15:.2f} on Sonnet")
    noabs = [p for p in papers if needs_call(p) and len(p.get("abstract", "")) < 120]
    if noabs:
        print(f"  {len(noabs)} have no usable abstract and will be flagged, not labelled:")
        for p in noabs[:8]:
            print(f"    · {(p.get('title') or '')[:66]}")
    return todo


# ─── calibration ─────────────────────────────────────────────────────────────
# The failure mode you cannot see from the output: you change the prompt, or a
# model version shifts, and labels quietly drift. A gold set catches that.
# Build one with --make-gold after you have hand-checked ~25 papers in the viewer.
def make_gold(papers, path):
    gold = [{"doi": p["doi"], "title": p["title"], "labels": p["labels"]}
            for p in papers if p.get("human_verified") and p.get("doi")]
    if not gold:
        print("  no human_verified papers yet — fix some labels in the viewer first")
        return
    json.dump(gold, open(path, "w"), ensure_ascii=False, indent=1)
    print(f"  wrote {path} with {len(gold)} hand-checked papers")


def check_gold(papers, path, lang):
    if not os.path.exists(path):
        return
    gold = {g["doi"].lower(): g for g in json.load(open(path))}
    subset = [p for p in papers if (p.get("doi") or "").lower() in gold
              and len(p.get("abstract", "")) >= 120]
    if not subset:
        return
    print(f"\ncalibration: re-labelling {len(subset)} gold papers from scratch...")
    key = os.environ.get("ANTHROPIC_API_KEY")
    agree = defaultdict(lambda: [0, 0])
    misses = []
    for p in subset:
        try:
            out = call_claude(build_prompt(p, lang), key)
        except Exception:
            continue
        want = gold[p["doi"].lower()]["labels"]
        for ax, v in want.items():
            got = out.get("labels", {}).get(ax)
            agree[ax][1] += 1
            if got == v:
                agree[ax][0] += 1
            else:
                misses.append((ax, p["title"][:44], v, got))
        time.sleep(0.2)
    print("  agreement with your hand labels:")
    for ax, (ok, n) in agree.items():
        bar = "█" * int(20 * ok / n) if n else ""
        warn = "  <-- drifted" if n and ok / n < 0.8 else ""
        print(f"    {ax:20s} {ok}/{n} {bar}{warn}")
    for ax, t, v, got in misses[:10]:
        print(f"      {ax}: {t} — you: {v} / model: {got}")



# ═════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zotero", required=True, help="Zotero export: .csv, .json (CSL), or .bib")
    ap.add_argument("--out", default="papers.json")
    ap.add_argument("--lang", default="English")
    ap.add_argument("--no-label", action="store_true")
    ap.add_argument("--no-enrich", action="store_true", help="trust the Zotero fields, skip OpenAlex")
    ap.add_argument("--tau", type=float, default=0.30,
                    help="author-similarity merge threshold; higher = smaller, tighter labs")
    ap.add_argument("--merge", metavar="PAPERS.JSON",
                    help="incremental: reuse labels and your hand edits from this file")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be labelled and what it costs, then stop")
    ap.add_argument("--relabel", nargs="*", default=[], metavar="AXIS",
                    help="force re-labelling of these axes (hand-verified papers are never touched)")
    ap.add_argument("--gold", default="gold.json", help="calibration set path")
    ap.add_argument("--make-gold", action="store_true",
                    help="write the calibration set from papers you have verified in the viewer")
    ap.add_argument("--check-gold", action="store_true",
                    help="re-label the calibration set and report drift")
    a = ap.parse_args()

    print("reading Zotero export...")
    items = read_zotero(a.zotero)
    if not items:
        sys.exit("no items found")

    if a.dry_run and not a.no_enrich:
        print("  (dry run: skipping OpenAlex — estimating from Zotero's own abstracts)")
        a.no_enrich = True

    if a.no_enrich:
        papers = [dict(it,
                       id=(it["doi"] or it["title"][:60]).replace("/", "_").replace(" ", "_"),
                       author_ids=it["authors"], cited_by=0, refs=[],
                       first_author=it["authors"][0] if it["authors"] else "",
                       last_author=it["authors"][-1] if it["authors"] else "")
                  for it in items]
    else:
        print("enriching from OpenAlex...")
        papers = enrich(items)
        print("fetching tldrs...")
        add_tldr(papers)

    stats = {"new": len(papers), "cached": 0, "dropped": 0, "repaired": 0}
    if a.merge:
        print("merging with existing database...")
        papers, stats = merge_existing(papers, a.merge, a.relabel)

    if a.make_gold:
        make_gold(papers, a.gold)
        return

    todo = estimate(papers)
    if a.dry_run:
        print("\n  dry run — nothing was sent to the API")
        return

    if a.no_label:
        for p in papers:
            p.setdefault("labels", {}); p.setdefault("confidence", {}); p.setdefault("phenomena", [])
            p.setdefault("significance", p.get("tldr", ""))
            p["needs_review"] = True
    elif todo or any(needs_call(p) for p in papers):
        print("labelling...")
        label_papers([p for p in papers if needs_call(p)], a.lang)
    else:
        print("labelling: nothing new")

    if a.check_gold:
        check_gold(papers, a.gold, a.lang)

    print("clustering by weighted author similarity...")
    cluster_authors(papers, a.tau)
    links = find_lineage(papers)

    for p in papers:
        p.pop("refs", None); p.pop("author_ids", None)

    db = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M"), "n": len(papers),
                   "axes": [{"id": k, "label": v["label"], "values": list(v["values"])}
                            for k, v in TAXONOMY.items()],
                   "multi_axes": [{"id": k, "label": v["label"], "values": v["values"]}
                                  for k, v in MULTI.items()],
                   "lineage": links,
                   "weights": {"last": W_LAST, "first": W_FIRST, "penultimate": W_PENULT,
                               "second": W_SECOND, "middle": W_MID, "tau": a.tau}},
          "papers": papers}
    json.dump(db, open(a.out, "w"), ensure_ascii=False, indent=1)

    nc = len({p["lab_cluster"] for p in papers})
    flagged = sum(1 for p in papers if p.get("needs_review"))
    verified = sum(1 for p in papers if p.get("human_verified"))
    print(f"\nwrote {a.out}  ·  {len(papers)} papers  ·  {nc} clusters")
    print(f"  {stats['cached']} reused · {stats['new']} newly labelled · {stats['dropped']} dropped")
    print(f"  {flagged} flagged for review · {verified} verified by you")
    for l in links:
        print(f"  lineage: {l['person']} — {', '.join(l['trained_in'])} -> {', '.join(l['now_leads'])}")


if __name__ == "__main__":
    main()
