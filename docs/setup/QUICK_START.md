# 🚀 SourceMind - Quick Start Guide

## Prerequisites

✅ **You Have:**
- Node.js v22.19.0
- npm v10.9.3

❌ **You Need (Optional):**
- Docker Desktop (for containerized setup)
- PostgreSQL 16+ with pgvector extension (for local database)

---

## 🎯 Quick Start (Frontend Only - For UI Development)

This is the **fastest way** to see the new design system and components in action:

### 1. Install Frontend Dependencies
```bash
cd frontend
npm install
```

### 2. Set Up Environment Variables
```bash
# Copy the example env file
cp .env.example .env
```

Edit `frontend/.env`:
```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:3001
```

### 3. Run Frontend Development Server
```bash
npm run dev
```

### 4. Open Your Browser
Navigate to: **http://localhost:3000**

You'll see:
- ✅ New landing page with gradient design
- ✅ Design system components
- ✅ Dark mode toggle (click the moon/sun icon)
- ✅ Responsive layout

**Note:** Backend API calls won't work yet (you'll see errors in console), but you can see all the UI improvements!

---

## 🐘 Full Stack Setup (With Database)

### Option A: Using Docker (Recommended)

#### 1. Install Docker Desktop
Download from: https://www.docker.com/products/docker-desktop/

#### 2. Copy Environment Files
```bash
# In project root
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

#### 3. Configure Backend Environment
Edit `backend/.env`:
```env
DATABASE_URL=postgresql://sourcemind:sourcemind@db:5432/sourcemind
OPENAI_API_KEY=sk-your-key-here  # Get from https://platform.openai.com
JWT_SECRET=your-super-secret-jwt-key-change-this
PORT=3001
```

#### 4. Start All Services
```bash
docker-compose up --build
```

#### 5. Run Database Migrations (First Time Only)
```bash
# In a new terminal
docker-compose exec backend npx prisma migrate dev --name init
docker-compose exec backend npm run seed
```

#### 6. Access the Application
- **Frontend:** http://localhost:3000
- **Backend API:** http://localhost:3001
- **Database:** localhost:5432

---

### Option B: Local Setup (Without Docker)

#### 1. Install PostgreSQL with pgvector

**Windows:**
```bash
# Download PostgreSQL 16 from:
https://www.postgresql.org/download/windows/

# After installation, install pgvector:
# Follow: https://github.com/pgvector/pgvector#installation
```

**macOS:**
```bash
brew install postgresql@16
brew install pgvector
```

**Linux:**
```bash
sudo apt-get install postgresql-16 postgresql-16-pgvector
```

#### 2. Create Database
```bash
# Connect to PostgreSQL
psql -U postgres

# Create database and enable extension
CREATE DATABASE sourcemind;
\c sourcemind
CREATE EXTENSION vector;
\q
```

#### 3. Install Backend Dependencies
```bash
cd backend
npm install
```

#### 4. Configure Backend Environment
Edit `backend/.env`:
```env
DATABASE_URL=postgresql://postgres:password@localhost:5432/sourcemind
OPENAI_API_KEY=sk-your-key-here
JWT_SECRET=your-super-secret-jwt-key-change-this
PORT=3001
```

#### 5. Run Database Migrations
```bash
cd backend
npx prisma migrate dev --name init
npm run seed
```

#### 6. Start Backend Server
```bash
cd backend
npm run start:dev
```

#### 7. Start Frontend Server (New Terminal)
```bash
cd frontend
npm run dev
```

#### 8. Access the Application
- **Frontend:** http://localhost:3000
- **Backend API:** http://localhost:3001

---

## 🎨 Testing the Design System

Once the frontend is running, you can test:

### 1. **Home Page**
- Visit http://localhost:3000
- See the new gradient hero section
- Check responsive design (resize browser)
- Test dark mode toggle (moon/sun icon in navbar)

### 2. **Design System Components**
The following components are updated:
- **MemoryCard** - Enhanced with badges and importance visualization
- **AttributionBar** - Shows contributor breakdown
- **EditHistoryList** - Timeline view with relative timestamps
- **NavBar** - Sticky header with dark mode toggle
- **DarkModeToggle** - Theme switcher

### 3. **Dark Mode**
- Click the moon icon (🌙) in the navbar
- Page switches to dark theme
- Refresh page - theme persists (localStorage)
- Click sun icon (☀️) to switch back

### 4. **Responsive Design**
Test at different screen sizes:
- **Mobile:** 320px - 640px
- **Tablet:** 768px - 1024px
- **Desktop:** 1280px+

---

## 🔧 Troubleshooting

### Frontend won't start
```bash
# Clear node_modules and reinstall
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run dev
```

### Backend won't start
```bash
# Check if PostgreSQL is running
# Windows: Check Services
# macOS/Linux: 
sudo systemctl status postgresql

# Regenerate Prisma client
cd backend
npx prisma generate
npm run start:dev
```

### Database connection errors
```bash
# Check DATABASE_URL in backend/.env
# Make sure PostgreSQL is running
# Verify database exists:
psql -U postgres -l
```

### Port already in use
```bash
# Frontend (port 3000)
# Kill process using port 3000
# Windows:
netstat -ano | findstr :3000
taskkill /PID <PID> /F

# macOS/Linux:
lsof -ti:3000 | xargs kill -9

# Backend (port 3001)
# Same process for port 3001
```

---

## 📝 Development Workflow

### Making Changes

1. **Edit Components**
   - Components are in `frontend/components/`
   - Changes hot-reload automatically

2. **Edit Styles**
   - Design system: `frontend/styles/globals.css`
   - Changes apply immediately

3. **Edit Backend**
   - Backend code: `backend/src/`
   - Server restarts automatically (watch mode)

4. **Database Changes**
   - Edit schema: `backend/prisma/schema.prisma`
   - Run migration:
     ```bash
     cd backend
     npx prisma migrate dev --name your_migration_name
     ```

### Committing Changes

```bash
# Make sure you're on dev branch
git checkout dev

# Add your changes
git add .

# Commit with descriptive message
git commit -m "feat: your feature description"

# Push to dev branch
git push origin dev
```

---

## 🌐 Environment Variables Reference

### Backend (`backend/.env`)
```env
# Database
DATABASE_URL=postgresql://user:password@host:port/database
DATABASE_DIRECT_URL=postgresql://user:password@host:port/database
SHADOW_DATABASE_URL=postgresql://user:password@host:port/database_shadow

# OpenAI (Required for embeddings and LLM features)
OPENAI_API_KEY=sk-xxxxx

# JWT Authentication
JWT_SECRET=your-super-secret-key-min-32-chars

# Server
PORT=3001
NODE_ENV=development

# Frontend URL (for CORS)
FRONTEND_URL=http://localhost:3000

# MCP
MCP_CONFIG_PATH=./mcp/manifest.json
```

### Frontend (`frontend/.env`)
```env
# API Base URL
NEXT_PUBLIC_API_BASE_URL=http://localhost:3001
```

---

## 🎯 What You Can Do Now

### ✅ **Working (Frontend Only)**
- View new landing page
- Test dark mode toggle
- See design system components
- Test responsive design
- Navigate between pages

### ⏳ **Requires Backend**
- User registration/login
- Create workspaces
- Create memories
- Search functionality
- Attribution tracking
- AI features

---

## 📚 Next Steps

1. **Start with frontend only** to see UI improvements
2. **Set up Docker** for full-stack development
3. **Get OpenAI API key** for AI features
4. **Read documentation:**
   - `DESIGN_SYSTEM.md` - Design system guide
   - `COMPONENT_UPDATES.md` - Component changes
   - `PROJECT_ROADMAP.md` - Future features

---

## 🆘 Need Help?

### Common Issues:
1. **"Cannot find module 'react'"** - Run `npm install` in frontend
2. **"Port 3000 already in use"** - Kill the process or use different port
3. **"Database connection failed"** - Check PostgreSQL is running
4. **"OPENAI_API_KEY not set"** - Add key to backend/.env

### Resources:
- **Next.js Docs:** https://nextjs.org/docs
- **NestJS Docs:** https://docs.nestjs.com
- **Prisma Docs:** https://www.prisma.io/docs
- **pgvector:** https://github.com/pgvector/pgvector

---

**Last Updated:** January 23, 2026  
**Version:** 0.1.0  
**Branch:** dev
