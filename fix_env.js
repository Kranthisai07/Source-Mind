const fs = require('fs');
const content = `DATABASE_URL="postgresql://postgres:Sourcemind%402025@db.nfcoafxsffyxexqhglmv.supabase.co:5432/postgres"
DATABASE_DIRECT_URL="postgresql://postgres:Sourcemind%402025@db.nfcoafxsffyxexqhglmv.supabase.co:5432/postgres"
SUPABASE_URL="https://nfcoafxsffyxexqhglmv.supabase.com"
SUPABASE_ANON_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5mY29hZnhzZmZ5eGV4cWhnbG12Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjkxOTIzNzAsImV4cCI6MjA4NDc2ODM3MH0.HHmeUtV6vZJgDTSZk_U6I8lv4eEMu9bGtwnePAyCeVo"
SUPABASE_SERVICE_ROLE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5mY29hZnhzZmZ5eGV4cWhnbG12Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTE5MjM3MCwiZXhwIjoyMDg0NzY4MzcwfQ.hkC6q04vFcSnHykNL9int_IukR2XKR0PZrhaMcZb_oE"
PORT=3001
NODE_ENV=development
JWT_SECRET="sourcemind-super-secret-jwt-key-change-this-in-production-min-32-chars"
MCP_CONFIG_PATH="./mcp/manifest.json"
FRONTEND_URL="http://localhost:3000"
`;
fs.writeFileSync('d:\\Source Mind\\backend\\.env', content, { encoding: 'utf8' });
console.log('Backend .env recreated successfully');
