# SourceMind Repository - Complete Overview

## 📋 Executive Summary

**SourceMind** is a production-oriented MVP that combines a **Supermemory-style universal memory engine** with **attribution intelligence** and **team graph features**. It solves two critical enterprise problems:

1. **Knowledge fragmentation** across team members
2. **Attribution opacity** in AI-assisted collaboration

The platform tracks not just WHAT your team knows, but WHO contributed it, HOW it evolved, and WHY decisions were made.

---

## 🏗️ Architecture Overview

### Technology Stack

**Backend:**
- **Framework:** NestJS (TypeScript)
- **Database:** PostgreSQL with pgvector extension
- **ORM:** Prisma
- **Authentication:** JWT (access + refresh tokens)
- **AI Integration:** OpenAI API (GPT-4o-mini)
- **Vector Search:** pgvector for semantic search

**Frontend:**
- **Framework:** Next.js 14 (App Router)
- **UI Library:** React 18
- **Data Fetching:** SWR + Axios
- **Styling:** CSS (minimal, utility-first approach)

**Infrastructure:**
- **Containerization:** Docker + Docker Compose
- **Services:** PostgreSQL (pgvector), Backend (NestJS), Frontend (Next.js)

---

## 📁 Repository Structure

```
d:\Source Mind\
├── backend/                    # NestJS backend application
│   ├── src/
│   │   ├── access-control/     # Role-based access control
│   │   ├── analytics/          # Attribution analytics
│   │   ├── attribution/        # Attribution tracking service
│   │   ├── auth/               # JWT authentication
│   │   ├── common/             # Shared utilities
│   │   ├── config/             # Environment validation
│   │   ├── conversations/      # Context-aware chat
│   │   ├── handoff/            # Knowledge handoff system
│   │   ├── llm/                # OpenAI provider abstraction
│   │   ├── mcp/                # Model Context Protocol
│   │   ├── memories/           # Core memory management
│   │   ├── prisma/             # Prisma service
│   │   ├── projects/           # Project management
│   │   ├── relations/          # Memory relationships
│   │   ├── users/              # User management
│   │   ├── workspaces/         # Workspace management
│   │   ├── app.module.ts       # Root module
│   │   └── main.ts             # Application entry
│   ├── prisma/
│   │   ├── schema.prisma       # Database schema
│   │   └── seed.ts             # Seed data
│   ├── Dockerfile
│   ├── package.json
│   └── .env.example
│
├── frontend/                   # Next.js frontend application
│   ├── app/
│   │   ├── login/              # Login page
│   │   ├── register/           # Registration page
│   │   ├── workspaces/         # Workspace & project views
│   │   ├── layout.tsx          # Root layout
│   │   ├── page.tsx            # Home page
│   │   └── globals.css         # Global styles
│   ├── components/
│   │   ├── AttributionBar.tsx  # Visual attribution display
│   │   ├── ChatPanel.tsx       # Chat interface
│   │   ├── EditHistoryList.tsx # Edit history viewer
│   │   ├── MemoryCard.tsx      # Memory display card
│   │   ├── NavBar.tsx          # Navigation bar
│   │   ├── ProjectDashboard.tsx# Project overview
│   │   └── RelationList.tsx    # Memory relations
│   ├── lib/
│   │   └── api.ts              # Axios API client
│   ├── Dockerfile
│   ├── package.json
│   └── .env.example
│
├── scripts/                    # Utility scripts
├── docker-compose.yml          # Multi-container orchestration
├── .gitignore
├── .env.example                # Shared environment variables
├── README.md                   # Setup & API documentation
└── Soure Mind Idea.md          # Original product vision

```

---

## 🗄️ Database Schema (Prisma)

### Core Models

#### **User**
- Authentication & profile management
- Relationships: workspace memberships, authored memories, conversations

#### **Workspace**
- Top-level organization unit
- Contains: members, projects, memories, access rules

#### **WorkspaceMember**
- Junction table for user-workspace relationships
- Roles: `owner`, `admin`, `member`

#### **Project**
- Sub-organization within workspaces
- Contains: memories, access rules, conversations

#### **Memory** (Core Entity)
- **Fields:**
  - `type`: document, note, message, decision, ai_output, code, handoff_summary
  - `source`: human, ai, human_ai_mixed
  - `content`: Main text content
  - `embedding`: Vector (1536 dimensions) for semantic search
  - `importanceScore`: Calculated relevance score
  - `metadata`: JSON for tags, custom data
- **Relationships:**
  - Attribution records (who contributed)
  - Edit history (change tracking)
  - Memory relations (links between memories)

#### **Attribution**
- Tracks contribution breakdown
- `contributorType`: user or tool
- Linked to specific memories

#### **EditHistory**
- Version control for memories
- `editorType`: user or tool
- `deltaSummary`: AI-generated change description
- Stores previous and new content

#### **MemoryRelation**
- Graph edges between memories
- Types: `relates_to`, `updates`, `supersedes`, `belongs_to`, `inspired_by`, `references`

#### **AccessControlRule**
- Fine-grained permissions
- Levels: `none`, `summary_only`, `read`, `write`, `admin`
- Scoped to workspace, project, or individual memory

#### **ConversationContext & ConversationMessage**
- Chat interface with memory context
- Stores conversation history per workspace/project

#### **RefreshToken**
- JWT refresh token management
- 7-day expiration

---

## 🔌 Backend API Endpoints

### Authentication (`/auth`)
- `POST /auth/register` - Create new user account
- `POST /auth/login` - Login with email/password
- `GET /auth/me` - Get current user profile
- `POST /auth/refresh` - Exchange refresh token for new access token

### Workspaces (`/workspaces`)
- `GET /workspaces` - List user's workspaces
- `POST /workspaces` - Create new workspace
- `GET /workspaces/:wid` - Get workspace details
- `GET /workspaces/:wid/projects` - List projects in workspace

### Projects (`/workspaces/:wid/projects`)
- `POST /workspaces/:wid/projects` - Create project
- `GET /workspaces/:wid/projects/:pid` - Get project details

### Memories (`/workspaces/:wid/memories`)
- `POST /workspaces/:wid/memories` - Create memory
- `PATCH /workspaces/:wid/memories/:id` - Update memory content
- `POST /workspaces/:wid/search` - Semantic search across memories
- `POST /workspaces/:wid/projects/:pid/memories/list` - List project memories

### Relations (`/workspaces/:wid/memories/:mid/relations`)
- `POST /workspaces/:wid/memories/:mid/relations` - Create memory relation
- `GET /workspaces/:wid/memories/:mid/relations` - List memory relations

### Attribution (`/workspaces/:wid/projects/:pid/attribution-summary`)
- `GET /workspaces/:wid/projects/:pid/attribution-summary` - Get contribution breakdown

### Conversations (`/workspaces/:wid/conversations`)
- `POST /workspaces/:wid/conversations` - Create conversation context
- `POST /workspaces/:wid/conversations/:cid/messages` - Add message with suggested context

### Knowledge Handoff (`/workspaces/:wid/knowledge-handoff`)
- `POST /workspaces/:wid/knowledge-handoff` - Generate knowledge transfer summary

### MCP (Model Context Protocol) (`/mcp`)
- `GET /mcp/manifest` - Get MCP tool manifest
- `POST /mcp/tools/:tool` - Execute MCP tool

---

## 🎨 Frontend Structure

### Pages

#### **Home (`/`)**
- Landing page with login/register links

#### **Login (`/login`)**
- Email/password authentication

#### **Register (`/register`)**
- New user registration

#### **Workspaces (`/workspaces`)**
- List of user's workspaces
- Create new workspace

#### **Workspace Detail (`/workspaces/[wid]`)**
- Project list
- Create new project

#### **Project View (`/workspaces/[wid]/projects/[pid]`)**
- Memory list
- Create/edit memories
- Attribution visualization
- Chat panel with context suggestions

### Components

#### **MemoryCard**
- Displays memory title, content preview, type, importance score
- Clickable for details

#### **AttributionBar**
- Visual list of contributors
- Color-coded segments for user vs tool

#### **EditHistoryList**
- Timeline of memory edits
- Shows editor, timestamp, delta summary

#### **RelationList**
- Graph of related memories
- Relationship types (supersedes, relates_to, etc.)

#### **ProjectDashboard**
- Overview of project metrics
- Top contributors
- Recent activity

#### **ChatPanel**
- Conversational interface
- Auto-suggests relevant memories as context

---

## 🔑 Key Features

### 1. **Attribution Tracking**
- **Contributor analysis:** Tracks user vs tool contributions
- **Edit history:** Full version control with AI-generated summaries
- **Maintenance:** Automatically updates attribution when memories are edited
- **Visual dashboard:** Contributor breakdowns

### 2. **Semantic Memory Search**
- **Vector embeddings:** Uses OpenAI embeddings (1536 dimensions)
- **pgvector integration:** Fast similarity search in PostgreSQL
- **Context-aware retrieval:** Filters by workspace, project, access level
- **Importance scoring:** Ranks memories by relevance

### 3. **Team Collaboration**
- **Workspaces & Projects:** Hierarchical organization
- **Role-based access:** Owner, admin, member, guest
- **Access levels:** None, summary_only, read, write, admin
- **Multi-user support:** Shared knowledge graphs

### 4. **Knowledge Handoff**
- **User transition:** Transfer knowledge when team members leave
- **Critical memory identification:** Flags high-importance memories
- **Auto-assignment:** Suggests new owners based on related work

### 5. **Memory Relations**
- **Graph structure:** Memories linked by relationships
- **Relation types:** Supersedes, updates, relates_to, inspired_by, etc.
- **Decision tracking:** Trace evolution of ideas

### 6. **Conversational Context**
- **Chat interface:** Ask questions about project knowledge
- **Auto-context:** Suggests relevant memories for chat
- **Memory-grounded responses:** AI answers based on team knowledge

### 7. **MCP Integration**
- **Model Context Protocol:** Standardized tool interface
- **Third-party clients:** Can integrate with external AI tools
- **Tool manifest:** Exposes memory operations as MCP tools

---

## 🚀 Getting Started

### Prerequisites
- Docker & Docker Compose
- Node.js (for local development)
- PostgreSQL (if not using Docker)

### Quick Start (Docker)

1. **Clone the repository**
   ```bash
   cd "d:\Source Mind"
   ```

2. **Copy environment files**
   ```bash
   cp backend/.env.example backend/.env
   cp frontend/.env.example frontend/.env
   ```

3. **Configure environment variables**
   - Edit `backend/.env`:
     - Set `OPENAI_API_KEY` (required for embeddings & LLM)
     - Set `JWT_SECRET` (long random string)
   - Edit `frontend/.env`:
     - Verify `NEXT_PUBLIC_API_BASE_URL=http://localhost:3001`

4. **Start services**
   ```bash
   docker-compose up --build
   ```

5. **Run database migrations** (first time only)
   ```bash
   docker-compose exec backend npx prisma migrate dev --name init
   docker-compose exec backend npm run seed
   ```

6. **Access the application**
   - Frontend: http://localhost:3000
   - Backend API: http://localhost:3001
   - Database: localhost:5432

### Local Development (Without Docker)

1. **Start PostgreSQL with pgvector**
   ```bash
   # Install pgvector extension
   # CREATE EXTENSION vector;
   ```

2. **Backend setup**
   ```bash
   cd backend
   npm install
   cp .env.example .env
   # Edit .env with your database URL
   npx prisma migrate dev
   npm run seed
   npm run start:dev
   ```

3. **Frontend setup**
   ```bash
   cd frontend
   npm install
   cp .env.example .env
   npm run dev
   ```

---

## 🔐 Authentication Flow

1. **Register:** `POST /auth/register` → Returns access + refresh tokens
2. **Login:** `POST /auth/login` → Returns access + refresh tokens
3. **Authenticated requests:** Include `Authorization: Bearer <accessToken>` header
4. **Token refresh:** `POST /auth/refresh` with refresh token → New token pair
5. **Access token:** 1 hour expiration
6. **Refresh token:** 7 day expiration

---

## 🧠 Memory Creation Flow

1. **User creates memory** via frontend or API
2. **Backend processes:**
   - Generates embedding using OpenAI API
   - Calculates importance score
   - Creates attribution record (user or tool)
   - Stores in PostgreSQL with vector
3. **Memory is searchable** via semantic search
4. **Edits tracked** in EditHistory table
5. **Attribution updated** on updates

---

## 📊 Attribution Algorithm

### Human Contribution Meter
- **Score Calculation:** Normalized distribution (0-1) across strictly human contributors.
- **AI Exclusion:** AI/Tool edits are tracked in history but excluded from the percentage calculation.
- **Metric:** Based on edit frequency and character delta magnitude.

### Edit Tracking
When a memory is edited:
1. Track new content and delta
2. Determine editor (user or tool)
3. If user: Update contribution scores (re-normalize)
4. If tool: Log in history, do not affect scores
5. Update edit history

---

## 🔍 Search & Retrieval

### Semantic Search
1. **Query embedding:** Convert search query to vector
2. **Cosine similarity:** Compare with memory embeddings
3. **Access control:** Filter by user permissions
4. **Ranking:** Sort by similarity + importance score
5. **Return top N:** Default 5 results

### Filters
- **Workspace:** Scope to specific workspace
- **Project:** Narrow to project memories
- **Type:** Filter by memory type (decision, note, etc.)
- **Access level:** Enforce read permissions

---

## 🎯 Use Cases

### 1. **Engineering Teams**
- Track architecture decisions
- Attribute code design choices
- Handoff knowledge when engineers leave

### 2. **Product Teams**
- Document feature requirements
- Track decision evolution
- Cross-team knowledge sharing

### 3. **Consulting Firms**
- Client knowledge bases
- Attribution for billing
- Project handoffs

### 4. **Research Labs**
- Academic integrity tracking
- Collaboration attribution
- Grant reporting

---

## 🛠️ Key Services

### **MemoriesService**
- `create()`: Create new memory with embedding
- `updateContent()`: Edit memory, track changes
- `search()`: Semantic vector search
- `listByProject()`: Get all project memories

### **AttributionService**
- `projectSummary()`: Aggregate contribution stats
- Returns: Contributors, totals (user/tool), top topics

### **LlmProvider**
- `generate()`: OpenAI chat completion
- Used for: Edit summaries, knowledge handoff

### **EmbeddingProvider**
- `embed()`: Generate vector embeddings
- Uses: OpenAI text-embedding-3-large

### **AccessControlService**
- `assertAccess()`: Verify user permissions
- `getAccessLevel()`: Check user's access to resource

### **HandoffService**
- `handoff()`: Generate knowledge transfer summary
- Identifies critical memories for departing user

### **McpService**
- `exec()`: Execute MCP tool commands
- Exposes memory operations to external clients

---

## 📈 Future Enhancements (from README)

- **Richer access control UI:** Rule editing interface
- **Enhanced attribution:** Edit history analysis
- **Decision views:** Supersede chains, graph visualizations
- **Hybrid search:** Keyword + vector search
- **Pagination & caching:** Performance optimization
- **Token rotation:** Refresh token revocation on logout
- **Expanded MCP:** More tool schemas, validation

---

## 🐛 Known Limitations

1. **No real-time collaboration:** Edits not synced live
2. **Basic UI:** Minimal styling, needs design polish
3. **No file attachments:** Text-only memories
4. **Limited analytics:** Basic contribution stats
5. **No mobile app:** Web-only interface
6. **Single LLM provider:** OpenAI only (no Claude, Gemini)

---

## 📚 Related Documentation

- **README.md:** Setup instructions, API summary
- **Soure Mind Idea.md:** Original product vision, business model, roadmap
- **backend/prisma/schema.prisma:** Complete database schema
- **backend/src/mcp/manifest.json:** MCP tool definitions

---

## 🤝 Contributing

This is a private repository. For development:

1. Create feature branches from `main`
2. Follow NestJS/Next.js conventions
3. Run Prisma migrations for schema changes
4. Update API documentation in README
5. Test with Docker Compose before committing

---

## 📝 Environment Variables

### Backend (`backend/.env`)
```env
DATABASE_URL=postgresql://sourcemind:sourcemind@db:5432/sourcemind
OPENAI_API_KEY=sk-xxxxx
JWT_SECRET=replace-with-long-secret
PORT=3001
MCP_CONFIG_PATH=./mcp/manifest.json
FRONTEND_URL=http://localhost:3000
```

### Frontend (`frontend/.env`)
```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:3001
```

---

## 🎓 Learning Resources

### Technologies Used
- **NestJS:** https://docs.nestjs.com
- **Prisma:** https://www.prisma.io/docs
- **pgvector:** https://github.com/pgvector/pgvector
- **Next.js App Router:** https://nextjs.org/docs
- **OpenAI Embeddings:** https://platform.openai.com/docs/guides/embeddings

### Concepts
- **Vector Search:** Semantic similarity using embeddings
- **Attribution Tracking:** Contribution analysis in collaborative work
- **Knowledge Graphs:** Relational memory structures
- **RAG (Retrieval-Augmented Generation):** Context-aware AI responses

---

## 📞 Support

For questions or issues:
1. Check README.md for setup instructions
2. Review Prisma schema for data model
3. Inspect API endpoints in controller files
4. Check Docker logs: `docker-compose logs -f`

---

**Last Updated:** January 2026  
**Version:** 0.1.0 (MVP)  
**License:** Private
