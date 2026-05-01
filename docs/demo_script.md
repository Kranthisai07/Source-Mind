# SourceMind — Technology Day Demo Script

> Target length: **8–10 minutes** + 2–3 min Q&A
> Audience: Industry experts (engineering leaders, infrastructure architects)
> Mode: Frontend in mock-data mode, Slack bot optional

---

## 0 · Before you start (30 sec setup)

- Frontend running: http://localhost:3000 (already on Dashboard)
- Slack tab open in a second window (optional, only if you want to show the bot)
- Close DevTools, email, Slack notifications — full-screen browser
- Zoom level at 100%, dark theme

**Anxiety reset:** everything is mock data. Nothing will fail on the network. The backend is real (FastAPI + Postgres + pgvector) — just not serving this demo.

---

## 1 · Opening hook (30 sec)

**Say:**
> "Every engineering team has the same problem: the person who understood the hardest part of the system just left. Or is about to leave. Or is on vacation during an incident.
>
> SourceMind is a memory layer for engineering teams. It watches your GitHub, Discord, and docs, extracts facts with attribution, detects contradictions, and — when someone leaves — classifies what they knew by risk."

**Do not:**
- Call it "an AI-powered knowledge base" (everyone has those)
- Start with architecture
- Apologize for mocks

---

## 2 · Dashboard (90 sec) — *"Here's what a team's brain looks like"*

**Click:** Should already be on `/dashboard`. If not, sidebar → Dashboard.

**Point to the four metric cards (top row), say:**
> "142 memories across 8 contributors, 47 new this month, 3 open conflicts. These are fact-level records — not documents. Each one is a single extracted decision, architecture choice, or incident learning."

**Point to the Health Gauge (big circular 82), say:**
> "This is a **knowledge health score**. It's not vanity metrics. It's a weighted composite: coverage (how much is documented), freshness (is it stale), conflict ratio (are memories contradicting each other), and attribution coverage (do we actually know who wrote this)."

**👉 Aha moment:** hover over the **Recent Activity** panel (right side). Say:
> "Every memory edit, every conflict detected, every handoff — it's all tracked. This is the audit layer for team knowledge."

**Click:** scroll down to **Who Would Know?** widget — this is the transition to the next section.

---

## 3 · Who Would Know? (90 sec) — *The viral feature*

> This is the feature to **linger on**. It's the most defensible and most differentiated thing in the product.

**Say:**
> "Slack has search. Notion has search. Every company has search. But nobody searches for *documents* in a panic — they search for *people*. 'Who knows about the auth migration?' 'Who set up the rate limiter?' That's what this answers."

**Click:** In the search box, type slowly: `database migrations`

**Wait for results to appear**, then:
> "Dmitry has 94% confidence — he wrote 7 memories on this topic. Look at the preview below his name — it's the most relevant memory he authored. You can see the category, his peak attribution, and a confidence bar."

**Click:** Clear the box with the X, then click the suggestion chip **"incident response"**.

**👉 Aha moment:**
> "This isn't Slack search. This is attribution math. For every matching memory, we sum the author's attribution score weighted by how well the memory matches the query. It's **reverse expertise lookup** — you ask a question, you get a person."

**Say while they're still reacting:**
> "Press `/` from anywhere on the page to focus this. Engineers never leave this page."

---

## 4 · Memories (60 sec) — *The core*

**Click:** Sidebar → **Memories**

**Say:**
> "These are the extracted memories. Each one is a single fact, with its source, its tags, a category, an attribution bar, and timestamps."

**Click:** Type `postgres` in the search bar. Watch it filter.

**Point to the attribution bar** (the thin colored bar in each card), say:
> "This isn't just 'lina wrote this.' It's a weighted attribution across five signals: character-level diff, semantic similarity via SBERT, temporal recency, entity overlap, and explicit approval events. When multiple people contribute, the bar splits proportionally."

**Click:** one memory card to open detail view.

**Point to the pie chart + breakdown table**, say:
> "Here's the attribution breakdown. You can see the five raw signals. This is what makes it defensible — not just a Git blame, but actual intellectual ownership."

**Click:** Back to Memories list.

---

## 5 · Handoff (90 sec) — *The business case*

**Click:** Sidebar → **Handoff**

**Say:**
> "This is the feature that turns a nice-to-have into a must-have. When an engineer leaves, their memories get classified into three tiers."

**Click:** In the Initiate Handoff form:
- Departing member: **Raj Patel**
- Transfer to: **Marcus Okafor**
- Departure date: any future date
- Notes: can skip
- Click **Classify & Initiate**

**Wait for the result panel**, then say:
> "Tier 1 is CRITICAL — importance > 0.8 AND solo-authored. These need a human in the loop before departure. Tier 2 is IMPORTANT. Tier 3 is standard documentation.
>
> For each Tier 1 memory, we suggest a successor based on who else has related expertise. 40% of attribution transfers on assignment — the original author stays in the record, but the new owner is now accountable."

**👉 Aha moment:**
> "Every company has tribal knowledge. Nobody measures it until someone leaves. This measures it **before**."

---

## 6 · Analytics (60 sec) — *The leadership view*

**Click:** Sidebar → **Analytics**

**Say:**
> "This is the view for engineering leaders. Same health score, but with breakdown descriptions."

**Click:** Tab → **Contribution Map**

**Point to the treemap**, say:
> "Size is memory count, color is the contributor's palette. At a glance you see concentration risk — if one block is gigantic, that person is a bus factor."

**Click:** Tab → **Knowledge Gaps**

**Point to the HIGH risk gap cards**, say:
> "Three gap types: single-contributor knowledge, stale important memories, and high-conflict tag areas. Every gap has a specific recommended action — this is what the leader acts on in their weekly 1:1s."

---

## 7 · Conflicts (45 sec) — *The unique differentiator*

**Click:** Sidebar → **Conflicts**

**Say:**
> "This is what happens when two memories contradict. We detect it automatically with an embedding similarity check plus a Claude Sonnet 4.6 LLM pass."

**Click:** any conflict card.

**Point to Memory A vs Memory B**, say:
> "Two memories about the same thing, saying different things. Claude drafted a suggested resolution. The reviewer picks: accept A, accept B, merge both, mark one outdated, or defer."

**👉 Aha moment:**
> "Every wiki has stale contradictions nobody notices. We surface them automatically."

**Click:** Back.

---

## 8 · Slack Bot (60 sec) — *Optional, only if ingested data exists*

> Skip this section if the database is empty — the bot will say "no memories found" and kill the momentum.

**Switch to Slack window.**

**Say:**
> "Nobody opens the SourceMind dashboard when they have a question. They open Slack. So the bot lives there."

**Type in a channel:** `/sourcemind rate limiting`

**When results appear:**
> "Same hybrid search, Block Kit formatted, with deep links back to the dashboard for full context. You can also ask `/sourcemind who knows about X` for the expertise lookup."

---

## 9 · Close (30 sec)

**Say:**
> "Under the hood: FastAPI and Python 3.14 on the backend, Postgres 16 with pgvector for semantic search, BM25 tsvector for keyword, fused with Reciprocal Rank Fusion. Claude Sonnet 4.6 for fact extraction and conflict resolution. Attribution is a 5-signal weighted model.
>
> 163 tests passing. Real GitHub and Discord connectors. Slack bot in Socket Mode.
>
> Built solo in [X weeks]. Thank you."

---

## Anticipated questions (memorize the 1-sentence answer for each)

**Q: How is this different from Glean / Notion AI / Mem?**
> "They index documents. We extract facts, attribute them, and detect contradictions. The Handoff workflow is unique — nobody else is measuring bus factor."

**Q: How does attribution work when I edit someone else's memory?**
> "Five signals with weighted contribution. The original author never disappears — we append an Attribution record. Appending is enforced by a DB trigger."

**Q: What's the latency?**
> "Hybrid search is sub-200ms p95. Attribution scoring is async via Celery, so ingestion returns a 202 immediately."

**Q: How do you prevent hallucinated facts?**
> "Every extracted fact has a source pointer back to the original document span. The LLM can't write a fact without citing its source text."

**Q: What stops this from turning into a political drama — 'why did X get credit and not me'?**
> "Good question. That's why attribution is append-only and transparent — the full breakdown is visible per memory. And the Handoff workflow transfers 40% of attribution share, not 100% — the original author stays in the record."

**Q: Scale?**
> "pgvector with HNSW indexing handles ~10M memories per workspace before needing sharding. The hot path is Redis-cached."

**Q: What about privacy — we don't want our decisions going to OpenAI."
> "Embeddings run on OpenAI today. Production deployment would use self-hosted sentence-BERT. The Claude calls for extraction can route through Bedrock for SOC 2."

**Q: Have you talked to users?**
> Be honest: "This is a research prototype. The core hypothesis — that handoff and expertise lookup are the killer features — came from [conversations with ___]. Validation is my next step."

---

## If something breaks

- **Blank page:** hit refresh. Mock data reloads instantly.
- **Slack bot errors:** say "the bot's in local dev mode tonight, let me show you the Dashboard view instead" and skip.
- **Hot reload froze:** close tab, reopen http://localhost:3000. 2 seconds.
- **Typo in search:** laugh and retype. They're humans.

---

## What NOT to do

- Don't open DevTools
- Don't demo the API directly (curl, Swagger) — too technical
- Don't explain the ingestion pipeline in depth unless asked
- Don't apologize for being solo-built — it's a strength
- Don't say "this is just mock data" unless directly asked — all products use synthetic data in demos

---

## Confidence boosters (read these 5 minutes before)

- 163 tests pass
- The Who Would Know? feature is genuinely novel
- The Handoff tier classification is a real business problem
- The frontend is polished — industry experts have seen uglier products at Series A
- You built this entire thing. That alone is impressive.
