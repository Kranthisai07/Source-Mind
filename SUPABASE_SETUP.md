# 🔥 Supabase Setup Guide for SourceMind

## 📋 Your Supabase Project

**Project URL:** https://nfcoafxsffyxexqhglmv.supabase.com

---

## 🎯 Step-by-Step Setup

### **Step 1: Get Your Database Password**

1. Go to your Supabase dashboard: https://nfcoafxsffyxexqhglmv.supabase.com
2. Click **Project Settings** (gear icon in sidebar)
3. Go to **Database** tab
4. Under **Connection string**, you'll see:
   ```
   postgresql://postgres:[YOUR-PASSWORD]@db.nfcoafxsffyxexqhglmv.supabase.co:5432/postgres
   ```
5. **Copy the password** (or reset it if you forgot)

### **Step 2: Get Your API Keys**

1. In Project Settings, go to **API** tab
2. Copy these keys:
   - **Project URL:** `https://nfcoafxsffyxexqhglmv.supabase.com`
   - **anon/public key:** (starts with `eyJ...`)
   - **service_role key:** (starts with `eyJ...`) - **Keep this secret!**

### **Step 3: Enable pgvector Extension**

1. In Supabase dashboard, go to **SQL Editor**
2. Click **New Query**
3. Paste and run:
   ```sql
   -- Enable vector extension for semantic search
   CREATE EXTENSION IF NOT EXISTS vector;
   
   -- Verify it's enabled
   SELECT * FROM pg_extension WHERE extname = 'vector';
   ```
4. Click **Run** (or press Ctrl+Enter)
5. You should see the vector extension listed

### **Step 4: Configure Backend Environment**

1. Copy the example file:
   ```bash
   cd backend
   cp .env.example .env
   ```

2. Edit `backend/.env` with your actual values:
   ```env
   # Supabase Database (replace [YOUR-PASSWORD] with actual password)
   DATABASE_URL=postgresql://postgres:[YOUR-PASSWORD]@db.nfcoafxsffyxexqhglmv.supabase.co:5432/postgres
   DATABASE_DIRECT_URL=postgresql://postgres:[YOUR-PASSWORD]@db.nfcoafxsffyxexqhglmv.supabase.co:5432/postgres
   
   # Supabase API
   SUPABASE_URL=https://nfcoafxsffyxexqhglmv.supabase.com
   SUPABASE_ANON_KEY=your-actual-anon-key
   SUPABASE_SERVICE_ROLE_KEY=your-actual-service-role-key
   
   # OpenAI (get from https://platform.openai.com/api-keys)
   OPENAI_API_KEY=sk-your-actual-openai-key
   
   # JWT Secret (generate a random string)
   JWT_SECRET=your-super-secret-jwt-key-min-32-chars-random-string
   
   # Server
   PORT=3001
   NODE_ENV=development
   
   # MCP
   MCP_CONFIG_PATH=./mcp/manifest.json
   
   # Frontend
   FRONTEND_URL=http://localhost:3000
   ```

### **Step 5: Install Backend Dependencies**

```bash
cd backend
npm install
```

### **Step 6: Run Database Migrations**

This will create all your tables in Supabase:

```bash
cd backend
npx prisma migrate deploy
```

Or if you want to create a new migration:

```bash
npx prisma migrate dev --name init
```

### **Step 7: Seed the Database (Optional)**

Add some initial data:

```bash
npm run seed
```

### **Step 8: Start the Backend**

```bash
npm run start:dev
```

You should see:
```
[Nest] LOG [NestApplication] Nest application successfully started
```

### **Step 9: Test the Connection**

Open a new terminal and test:

```bash
curl http://localhost:3001/auth/me
```

Or visit in browser: http://localhost:3001

---

## 🔍 **Verify Everything Works**

### **Check Database Connection**

```bash
cd backend
npx prisma studio
```

This opens Prisma Studio at http://localhost:5555 where you can see your database tables.

### **Check Tables Were Created**

In Supabase dashboard:
1. Go to **Table Editor**
2. You should see tables like:
   - User
   - Workspace
   - Project
   - Memory
   - Attribution
   - EditHistory
   - etc.

---

## 🎨 **Update Frontend to Use Supabase (Optional)**

If you want to use Supabase client in frontend:

### **Install Supabase Client**

```bash
cd frontend
npm install @supabase/supabase-js
```

### **Create Supabase Client**

Create `frontend/lib/supabase.ts`:

```typescript
import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

export const supabase = createClient(supabaseUrl, supabaseAnonKey);
```

### **Update Frontend .env**

Edit `frontend/.env`:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:3001
NEXT_PUBLIC_SUPABASE_URL=https://nfcoafxsffyxexqhglmv.supabase.com
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key-here
```

---

## 🚀 **Running the Full Stack**

### **Terminal 1: Backend**
```bash
cd backend
npm run start:dev
```

### **Terminal 2: Frontend** (already running)
```bash
cd frontend
npm run dev
```

### **Access:**
- **Frontend:** http://localhost:3000
- **Backend API:** http://localhost:3001
- **Prisma Studio:** http://localhost:5555 (run `npx prisma studio`)
- **Supabase Dashboard:** https://nfcoafxsffyxexqhglmv.supabase.com

---

## 🔧 **Troubleshooting**

### **"Connection refused" error**

Check your DATABASE_URL:
- Password is correct
- No spaces in the connection string
- Using `postgres` as database name (not `sourcemind`)

### **"Extension vector does not exist"**

Run in Supabase SQL Editor:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### **"Migration failed"**

1. Check database connection
2. Make sure pgvector is enabled
3. Try resetting:
   ```bash
   npx prisma migrate reset
   ```

### **"Cannot find module '@supabase/supabase-js'"**

Install it:
```bash
cd frontend
npm install @supabase/supabase-js
```

---

## 📊 **What You Get with Supabase**

### ✅ **Included Features:**

1. **PostgreSQL Database**
   - Fully managed
   - Automatic backups
   - pgvector extension

2. **Real-time Subscriptions**
   - Listen to database changes
   - Live collaboration features

3. **Authentication**
   - Email/password
   - Social logins (Google, GitHub, etc.)
   - Magic links

4. **Storage**
   - File uploads
   - Image optimization
   - CDN delivery

5. **Edge Functions**
   - Serverless functions
   - Deploy backend logic

6. **Dashboard**
   - Table editor
   - SQL editor
   - Logs and monitoring

### 💰 **Free Tier Limits:**

- 500 MB database
- 1 GB file storage
- 2 GB bandwidth
- 50,000 monthly active users
- Unlimited API requests

**Perfect for development and MVP!**

---

## 🎯 **Next Steps After Setup**

1. **Test Authentication**
   - Register a user
   - Login
   - Create a workspace

2. **Test Memory Creation**
   - Create a memory
   - Search memories
   - Check vector search works

3. **Test Attribution**
   - Create memory with AI
   - Check attribution tracking
   - View edit history

4. **Enable Real-time** (Optional)
   - Add Supabase subscriptions
   - Live memory updates
   - User presence

---

## 📚 **Useful Supabase Resources**

- **Documentation:** https://supabase.com/docs
- **Guides:** https://supabase.com/docs/guides
- **pgvector Guide:** https://supabase.com/docs/guides/ai/vector-columns
- **Auth Guide:** https://supabase.com/docs/guides/auth
- **Storage Guide:** https://supabase.com/docs/guides/storage

---

## 🆘 **Need Help?**

### **Common Issues:**

1. **Can't connect to database**
   - Check password in .env
   - Verify Supabase project is active
   - Check firewall/network

2. **Migrations fail**
   - Enable pgvector extension
   - Check DATABASE_URL format
   - Try `npx prisma migrate reset`

3. **Tables not showing**
   - Run migrations: `npx prisma migrate deploy`
   - Check Supabase Table Editor
   - Refresh Prisma Studio

### **Get Your Credentials:**

1. **Database Password:**
   - Project Settings > Database > Reset database password

2. **API Keys:**
   - Project Settings > API > Copy keys

3. **Connection String:**
   - Project Settings > Database > Connection string (URI)

---

**Last Updated:** January 23, 2026  
**Project:** SourceMind  
**Supabase Project:** nfcoafxsffyxexqhglmv
