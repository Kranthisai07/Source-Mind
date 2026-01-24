-- Enable pgvector extension for vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify the extension is enabled
SELECT * FROM pg_extension WHERE extname = 'vector';

-- This should return a row showing the vector extension is installed
