# SourceMind MVP

Production-oriented MVP combining a Supermemory-style universal memory engine with attribution intelligence and team graph features.

## 📚 Documentation

**[→ Full Documentation](docs/README.md)**

Quick links:
- **[Quick Start Guide](docs/setup/QUICK_START.md)** - Get started in minutes
- **[Supabase Setup](docs/setup/SUPABASE_SETUP.md)** - Database configuration
- **[Project Roadmap](docs/project/PROJECT_ROADMAP.md)** - Development timeline
- **[CHANGELOG](docs/CHANGELOG.md)** - Version history

## Stack
- **Backend:** NestJS + Prisma + PostgreSQL (pgvector) + Supabase
- **Frontend:** Next.js (App Router) + React + SWR
- **Database:** Supabase PostgreSQL with pgvector extension
- **Auth:** JWT (access + refresh tokens)

## Run locally (docker-compose)
1. Copy envs:
   - `cp backend/.env.example backend/.env`
   - `cp frontend/.env.example frontend/.env`
2. `docker-compose up --build`
3. Run migrations & seed inside backend container (first time):
   - `docker-compose exec backend npx prisma migrate dev --name init`
   - `docker-compose exec backend npm run seed`
4. Frontend: http://localhost:3000, Backend: http://localhost:3001

## Auth
- JWT access (1h) + refresh (7d) tokens from `/auth/login` or `/auth/register`.
- `/auth/refresh` exchanges refresh for new pair.
- Authorization: `Authorization: Bearer <accessToken>`.

## Backend summary
- Auth: `/auth/register`, `/auth/login`, `/auth/me` (JWT)
- Refresh: `/auth/refresh`
- Workspaces/Projects: `/workspaces`, `/workspaces/:wid/projects`
- Memories:
  - Create: `POST /workspaces/:wid/memories`
  - Update: `PATCH /workspaces/:wid/memories/:id`
  - Search: `POST /workspaces/:wid/search`
  - Relations: `POST /workspaces/:wid/memories/:mid/relations`
  - List relations: `GET /workspaces/:wid/memories/:mid/relations`
  - List by project: `POST /workspaces/:wid/projects/:pid/memories/list`
- Conversations (context suggestions):
  - `POST /workspaces/:wid/conversations`
  - `POST /workspaces/:wid/conversations/:cid/messages`
- Attribution summary:
  - `GET /workspaces/:wid/projects/:pid/attribution-summary`
- Knowledge handoff:
  - `POST /workspaces/:wid/knowledge-handoff`
- MCP:
  - `GET /mcp/manifest`
  - `POST /mcp/tools/:tool`

## Frontend summary
- Pages: login/register, workspace list, project list/create, project memories (create/list), chat with suggested context.
- Components: MemoryCard, AttributionBar, RelationList, EditHistoryList, ProjectDashboard, ChatPanel.
- API client: `frontend/lib/api.ts` (axios to `NEXT_PUBLIC_API_BASE_URL`).

## Extension notes
- Add richer access control UI + rule editing.
- Enhance attribution (diff-based percent scoring) and embed LLM responses into chat.
- Add decision views with supersede chains and graph visualizations.
- Improve vector search (hybrid keyword), pagination, and caching.
- Implement refresh token rotation + revocation on logout.
- Expand MCP tool schemas and validation for third-party clients.
