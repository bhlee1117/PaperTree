# Sharing PaperTree

## First: do you need a server at all?

`dendrite_atlas.html` is one self-contained file, ~150 KB, with the data baked in. No
server, no internet. AirDrop it to your phone, put it in iCloud Drive, mail it to
yourself. It opens offline on anything with a browser.

Use a server when you want the *current* build always, without re-sending the file.

## Same wifi

```bash
cd ~/Documents/GitHub/PaperTree
python3 serve.py --lan --password whatever
```

Prints something like `http://10.243.18.7:8777`. Open that on your phone or laptop on
the same network. Stop with ctrl-C.

**Do not use `python3 -m http.server`.** It serves the whole directory, which here means
`.env` — your API key, fetchable by anyone who can reach the port — and `.git/`, every
version of everything you ever committed. `serve.py` can only ever return
`dendrite_atlas.html` and `atlas.json`; every other path is 404, including files you add
to the folder later.

Harvard wifi isolates clients on some networks, so phone-to-laptop may not connect even
when both are on it. If that happens, skip to Tailscale.

## From anywhere, privately — Tailscale

This is the one to use. It builds a private network between your own devices; nothing is
exposed to the public internet and there is no URL to leak.

```bash
brew install --cask tailscale     # then sign in, and install it on your phone too
python3 serve.py --lan --password whatever
tailscale ip -4                   # e.g. 100.101.102.103
```

Then `http://100.101.102.103:8777` from any device signed into your account, anywhere.

## From anywhere, publicly — Cloudflare tunnel

Only if you need to send a link to someone who is not you.

```bash
brew install cloudflared
python3 serve.py --password whatever      # localhost is enough; the tunnel reaches in
cloudflared tunnel --url http://localhost:8777
```

Prints a `https://something.trycloudflare.com` URL. It dies when you ctrl-C.

**Think before you do this.** The atlas is not a reading list. It contains your reading
of what is weak in the field, which claims rest on one paper, and where your own
published work is the contested evidence. That is pre-publication thinking. Keep the
password on, and prefer Tailscale.

## Keeping it up

A sleeping Mac serves nothing:

```bash
caffeinate -s python3 serve.py --lan --password whatever
```

To rebuild and restart in one go, run `./update.sh` first — `serve.py` reads the file
fresh on every request, so a rebuild is picked up without restarting the server.

## GitHub Pages — a real URL, no machine of yours running

```bash
./publish.sh          # shows what would go public, changes nothing
./publish.sh --go     # copies to docs/index.html, commits, pushes
```

Then once, on github.com: **Settings → Pages → Source: Deploy from a branch →
Branch: main, Folder: /docs → Save**. Live at
`https://<user>.github.io/PaperTree/` in a minute or two. After that,
`./update.sh && ./publish.sh --go` refreshes it.

Nothing to drag in. `build_atlas.py` bakes the data into `papertree.html`, and the page
also tries to fetch an `atlas.json` sitting beside it — `publish.sh` puts both in `docs/`.
Opened from disk the fetch is blocked and the baked copy is used, which is what makes the
file work offline; served over HTTP the fetch wins, so the page shows the newest data even
when the browser or GitHub's CDN is still holding a stale `index.html`. The header says
which one you are looking at: `atlas.json · live`, or `sample data` if you opened the
demo copy.

Refreshing the site is the same two commands every time:

```bash
./update.sh          # rebuilds papers.json, atlas.json, papertree.html
./publish.sh --go    # copies both into docs/, commits, pushes
```

`publish.sh` runs a dry run by default because the decision is the hard part, not the
mechanics. It prints the repo's visibility, lists what is already tracked and would
become public with it, and refuses outright if `.env` is in the history.

**On a public repo, Pages does not publish a page — it publishes the repository.**
`claims.yaml` and `papers.json` are tracked, and they are your reading of which claims
are thin and where your own work sits as contested evidence, with every past version in
the history. That may be exactly what you want for a lab-facing resource. It is not
what you want for working notes.

**On a private repo,** Pages needs a paid GitHub plan; on a free account the enable step
fails. Use the next option instead.

## A URL, but not for everyone — Cloudflare Pages + Access

Free, and the only option here that gives a real URL with a login in front of it.

```bash
brew install cloudflared
mkdir -p site && cp dendrite_atlas.html site/index.html
cloudflared pages deploy site --project-name papertree   # or drag the folder to dash.cloudflare.com
```

Then in the Cloudflare dashboard: **Zero Trust → Access → Add an application →
Self-hosted**, point it at the Pages URL, and add a policy allowing specific email
addresses. Visitors get a one-time code by email. The free tier covers 50 users, which
is more than a lab.

This is the right shape for "my collaborators can see it, the internet cannot."
