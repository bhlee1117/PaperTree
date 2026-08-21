#!/usr/bin/env python3
"""
build_atlas.py — merge claims.yaml + papers.json into atlas.json for the viewer.

    python3 build_atlas.py

Two sources of evidence edges, merged here:
  claims.yaml   hand-authored, source: human. The spine. Survives everything.
  papers.json   machine-proposed, source: model, written by assign_claims.py.

When both describe the same paper-claim pair the human edge wins and the model edge is
dropped. Matching is by citation key on the human side and DOI on the machine side, so
claims.yaml carries an optional `doi` per edge; edges without one are matched by key
against a slugified author-year and reported if they fail, rather than silently lost.

Also computes, per claim, the diagnostics that the surviving taxonomy exists to provide:
what preparations the evidence comes from, what modalities, what years, and which
methodological caveats it inherits from Q7.
"""
import itertools, json, os, re, sys, collections
import yaml
import build_db as B


def slug(name, year):
    return re.sub(r"[^a-z]", "", (name or "").split()[-1].lower()) + f"_{year}"


def main():
    cdoc = yaml.safe_load(open("claims.yaml"))
    src = sys.argv[1] if len(sys.argv) > 1 else "papers.json"
    db = json.load(open(src))
    papers = db["papers"]
    rules = cdoc["meta"].get("caveat_rules", {})
    if not any((p.get("labels") or {}).get("modality") for p in papers):
        print("  ! no paper carries a `modality` label. Caveat inheritance will be empty.\n"
              "    Re-run ./update.sh — this database was built under an older taxonomy.",
              file=sys.stderr)

    by_doi = {(p.get("doi") or "").lower(): p for p in papers if p.get("doi")}
    by_slug = {}
    for p in papers:
        if p.get("last_author") and p.get("year"):
            by_slug.setdefault(slug(p["last_author"], p["year"]), p)
        if p.get("first_author") and p.get("year"):
            by_slug.setdefault(slug(p["first_author"], p["year"]), p)

    edges, unresolved = [], []
    # 1. human edges from claims.yaml
    for c in cdoc["claims"]:
        for e in c.get("evidence", []):
            p = by_doi.get((e.get("doi") or "").lower()) or by_slug.get(e["key"])
            edges.append({
                "claim": c["id"], "key": e["key"],
                "paper_id": p["id"] if p else None,
                "stance": e["stance"], "strength": e["strength"],
                "region": e.get("region"), "modality": e.get("modality"),
                "flags": e.get("flags", []),
                "condition": e.get("condition", ""), "source": "human",
            })
            if not p:
                unresolved.append((c["id"], e["key"]))

    # 2. model edges from papers.json, unless a human edge already covers the pair
    human_pairs = {(x["claim"], x["paper_id"]) for x in edges if x["paper_id"]}
    dropped = 0
    for p in papers:
        for e in p.get("claim_edges") or []:
            if (e["claim"], p["id"]) in human_pairs:
                dropped += 1
                continue
            mods = {"patch": "patch", "voltage imaging": "voltage",
                    "calcium imaging": "calcium", "modeling": "model",
                    "review": "review"}
            edges.append({
                "claim": e["claim"], "key": p["id"], "paper_id": p["id"],
                "stance": e["stance"], "strength": e["strength"],
                "region": None,
                "modality": mods.get((p.get("labels") or {}).get("modality"), "other"),
                "flags": [f for f in p.get("method_flags", [])
                          if f in ("optogenetics", "anesthetized", "room temperature")],
                "condition": e.get("condition", ""), "quote": e.get("quote", ""),
                "source": e.get("source", "model"),
                "needs_review": e.get("needs_review", False),
            })

    # 3. per-claim diagnostics
    FLAGMAP = {"optogenetics": "optogenetic", "anesthetized": "anesthetized",
               "room temperature": "room_temp"}
    pid = {p["id"]: p for p in papers}
    claims = []
    for c in cdoc["claims"]:
        mine = [e for e in edges if e["claim"] == c["id"]]
        sup = [e for e in mine if e["stance"] in ("supports", "qualifies")]
        cav = collections.Counter()
        for e in sup:
            if e.get("modality") in rules and rules[e["modality"]] != c["id"]:
                cav[rules[e["modality"]]] += 1
            for f in e.get("flags", []):
                k = FLAGMAP.get(f, f)
                if k in rules and rules[k] != c["id"]:
                    cav[rules[k]] += 1
        yrs = [pid[e["paper_id"]].get("year") for e in mine
               if e["paper_id"] and pid[e["paper_id"]].get("year")]
        preps = collections.Counter(
            (pid[e["paper_id"]].get("labels") or {}).get("prep")
            for e in sup if e["paper_id"])
        claims.append({**c,
            "n": collections.Counter(e["stance"] for e in mine),
            "modalities": dict(collections.Counter(e.get("modality") for e in sup)),
            "preps": {k: v for k, v in preps.items() if k},
            "years": [min(yrs), max(yrs)] if yrs else None,
            "inherits": dict(cav),
            "direct_sources": len({e["key"] for e in sup if e["strength"] == "direct"}),
        })

    # 4. author-similarity graph for the network view.
    # Stored as raw pairs rather than a precomputed tree so the viewer can retune the
    # similarity floor and the year window without a rebuild — those two knobs are the
    # whole point of the view, and a baked-in tree would hide what they do.
    W = [B.author_weights(p.get("authors") or []) for p in papers]
    sim_edges = []
    for i, j in itertools.combinations(range(len(papers)), 2):
        a, b = papers[i], papers[j]
        if not (a.get("year") and b.get("year")):
            continue
        if abs(a["year"] - b["year"]) > 12:      # nothing beyond the widest usable window
            continue
        sc = B.wcosine(W[i], W[j])
        la, lb = B.surname(a.get("last_author", "")), B.surname(b.get("last_author", ""))
        if la and la == lb:
            sc = max(sc, 0.75)
        if sc >= 0.08:
            sim_edges.append([i, j, round(sc, 3)])
    print(f"  {len(sim_edges)} author-similarity pairs within 12 years")

    out = {"meta": {**db.get("meta", {}), **{k: v for k, v in cdoc["meta"].items()}},
           "questions": cdoc["questions"], "claims": claims,
           "edges": edges, "papers": papers, "sim_edges": sim_edges}
    json.dump(out, open("atlas.json", "w"), ensure_ascii=False, indent=1)

    # Bake the data straight into a standalone page. Dragging atlas.json in still works,
    # but nobody should have to remember it — and the built-in copy being the sample was
    # the difference between "my library" and "someone else's demo" on every open.
    tpl = "atlas_template.html"
    if os.path.exists(tpl):
        html = open(tpl, encoding="utf-8").read()
        blob = json.dumps(out, ensure_ascii=False)
        open("papertree.html", "w", encoding="utf-8").write(html.replace("/*__DATA__*/", blob))
        print(f"wrote papertree.html · {len(blob)//1024} KB of data baked in")
    else:
        print(f"  ! {tpl} not found — atlas.json written, but no standalone page built")

    # Which cited works are not yet in Zotero. This is a reading list, not an error.
    if unresolved:
        doi_of = {}
        for c in cdoc["claims"]:
            for e in c.get("evidence", []):
                if e.get("doi"):
                    doi_of[e["key"]] = e["doi"]
        with open("missing_papers.txt", "w") as f:
            for k in sorted({k for _, k in unresolved}):
                f.write(f"{doi_of.get(k, '')}\t{k}\n")
        print(f"  wrote missing_papers.txt · {len({k for _,k in unresolved})} works cited "
              f"by claims.yaml but absent from Zotero")

    n = collections.Counter(e["source"] for e in edges)
    print(f"wrote atlas.json · {len(claims)} claims · {len(edges)} edges {dict(n)}")
    print(f"  {dropped} model edges superseded by a human edge")
    if unresolved:
        print(f"  ! {len(unresolved)} human edges cite a paper not in the library:")
        for cid, k in unresolved[:12]:
            print(f"      {cid}  {k}")
        print("    add the DOI to claims.yaml, or add the paper to Zotero")


if __name__ == "__main__":
    main()
