const { Client } = require('pg');
require('dotenv').config();

async function enablePgVector() {
    const client = new Client({
        connectionString: process.env.DATABASE_DIRECT_URL,
    });

    try {
        await client.connect();
        console.log('Connected to database successfully!');

        // Enable pgvector extension
        await client.query('CREATE EXTENSION IF NOT EXISTS vector;');
        console.log('✓ pgvector extension enabled');

        // Verify extension
        const result = await client.query(
            "SELECT * FROM pg_extension WHERE extname = 'vector';"
        );

        if (result.rows.length > 0) {
            console.log('✓ pgvector extension verified');
            console.log(result.rows[0]);
        } else {
            console.log('⚠ Warning: pgvector extension not found');
        }

    } catch (error) {
        console.error('Error:', error.message);
        process.exit(1);
    } finally {
        await client.end();
    }
}

enablePgVector();
