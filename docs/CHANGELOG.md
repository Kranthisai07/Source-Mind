# Changelog

All notable changes to SourceMind will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-01-23

### Added
- **Supabase Integration**
  - Migrated from local PostgreSQL to Supabase managed database
  - Configured Session Pooler for connection pooling (`aws-0-us-east-1.pooler.supabase.com:6543`)
  - Created helper scripts for database setup (`enable-pgvector.js`, `setup-database.js`, `apply-migration.js`)
  - Added comprehensive Supabase setup documentation (`SUPABASE_SETUP.md`)

- **Authentication UI Improvements**
  - Complete redesign of Register page with proper spacing, labels, and styling
  - Complete redesign of Login page matching Register page design
  - Added cross-navigation links between Login and Register pages
  - Implemented design system components (buttons, inputs, cards)
  - Added styled error boxes for better error visibility

- **Documentation**
  - Created `REGISTRATION_LOGIN_FIXES.md` with authentication improvements
  - Updated `PROJECT_ROADMAP.md` with recent updates section
  - Created `CHANGELOG.md` for version tracking

### Changed
- **Backend**
  - Updated `PrismaService` to use `ConfigService` for database URL
  - Added logging to database connection process
  - Removed `McpModule` temporarily (commented out in `app.module.ts`)
  - Updated `.env` configuration for Supabase

- **Frontend**
  - Fixed API response handling in Register page (`res.data.accessToken` instead of `res.data.data.token`)
  - Fixed API response handling in Login page (same fix)
  - Added `refreshToken` storage in localStorage
  - Added console error logging for better debugging

### Fixed
- **Database Connection**
  - Resolved P1001 error by switching to Supabase Session Pooler
  - Fixed Prisma + PgBouncer compatibility issues
  - Enabled pgvector extension in Supabase

- **UI/UX**
  - Fixed cramped spacing on authentication forms
  - Added missing labels for form inputs
  - Improved error message display
  - Fixed button styling consistency

- **Authentication**
  - Fixed token storage mechanism
  - Fixed JWT token retrieval from API responses
  - Added refresh token support

## [0.1.0] - 2026-01-23

### Added
- Initial MVP release
- NestJS backend with modular architecture
- PostgreSQL database with pgvector extension
- Prisma ORM with comprehensive schema (16 models)
- Next.js 14 frontend with App Router
- JWT authentication (access + refresh tokens)
- Memory management (create, update, search)
- Vector embeddings (OpenAI 1536D)
- Semantic search with pgvector
- Attribution system with contribution tracking
- Workspace and project management
- Role-based access control (RBAC)
- Memory relations and knowledge graph
- Conversational interface
- MCP (Model Context Protocol) integration
- Design system implementation
- Dark mode support
- Comprehensive documentation

### Security
- JWT-based authentication
- Password hashing with bcrypt
- Role-based access control
- Environment variable validation

---

## Version History

- **0.1.1** (2026-01-23) - Supabase migration + Auth UI improvements
- **0.1.0** (2026-01-23) - Initial MVP release

---

## Upgrade Guide

### From 0.1.0 to 0.1.1

#### Backend
1. Update `.env` with Supabase connection strings:
   ```env
   DATABASE_URL="postgresql://postgres:YOUR_PASSWORD@aws-0-us-east-1.pooler.supabase.com:6543/postgres?pgbouncer=true"
   ```

2. Run database migrations:
   ```bash
   cd backend
   node apply-migration.js
   ```

3. Restart backend server:
   ```bash
   npm run start:dev
   ```

#### Frontend
1. Clear localStorage (to remove old token format):
   ```javascript
   localStorage.clear()
   ```

2. Restart frontend server:
   ```bash
   npm run dev
   ```

3. Test registration and login flows

---

**Maintained by:** SourceMind Development Team  
**Last Updated:** 2026-01-23
