-- Synthetic conflict fixtures. Re-runnable: wipes and recreates everything.
--
-- Tags on the A/B memories are deliberately NON-EMPTY. The question the split
-- case has to answer is whether resolution ADDS a tag or REPLACES the existing
-- ones; seeding empty arrays would make both behaviours look identical.
DELETE FROM memory_conflicts WHERE workspace_id = '00000000-0000-4000-8000-0000000000a0';
DELETE FROM memories        WHERE workspace_id = '00000000-0000-4000-8000-0000000000a0';

INSERT INTO memories (id, workspace_id, content, content_hash, tags) VALUES
 ('00000000-0000-4000-8000-00000000a001','00000000-0000-4000-8000-0000000000a0',
  'Vector index is IVFFlat with lists=100.', md5('m-a1'), ARRAY['index','legacy']),
 ('00000000-0000-4000-8000-00000000b001','00000000-0000-4000-8000-0000000000a0',
  'Vector index is HNSW with m=16, ef_construction=64.', md5('m-b1'), ARRAY['index']),

 ('00000000-0000-4000-8000-00000000a002','00000000-0000-4000-8000-0000000000a0',
  'Retention is 30 days for the staging environment.', md5('m-a2'), ARRAY['retention','staging']),
 ('00000000-0000-4000-8000-00000000b002','00000000-0000-4000-8000-0000000000a0',
  'Retention is 90 days for the production environment.', md5('m-b2'), ARRAY['retention']),

 ('00000000-0000-4000-8000-00000000a003','00000000-0000-4000-8000-0000000000a0',
  'Deploys are gated on a manual approval step.', md5('m-a3'), ARRAY['deploy']),
 ('00000000-0000-4000-8000-00000000b003','00000000-0000-4000-8000-0000000000a0',
  'Deploys are fully automatic once CI is green.', md5('m-b3'), ARRAY['deploy','ci']),

 ('00000000-0000-4000-8000-00000000a004','00000000-0000-4000-8000-0000000000a0',
  'Already-settled claim A.', md5('m-a4'), ARRAY['settled']),
 ('00000000-0000-4000-8000-00000000b004','00000000-0000-4000-8000-0000000000a0',
  'Already-settled claim B.', md5('m-b4'), ARRAY['settled']);

INSERT INTO memory_conflicts
 (id, workspace_id, memory_a_id, memory_b_id, conflict_type, severity, status,
  similarity_score, explanation)
VALUES
 -- merged
 ('00000000-0000-4000-8000-0000000c0001','00000000-0000-4000-8000-0000000000a0',
  '00000000-0000-4000-8000-00000000a001','00000000-0000-4000-8000-00000000b001',
  'contradiction','medium','open',0.91,'Two different vector index strategies.'),
 -- split
 ('00000000-0000-4000-8000-0000000c0002','00000000-0000-4000-8000-0000000000a0',
  '00000000-0000-4000-8000-00000000a002','00000000-0000-4000-8000-00000000b002',
  'contradiction','medium','open',0.87,'Retention differs; may be per-environment.'),
 -- deferred
 ('00000000-0000-4000-8000-0000000c0003','00000000-0000-4000-8000-0000000000a0',
  '00000000-0000-4000-8000-00000000a003','00000000-0000-4000-8000-00000000b003',
  'contradiction','critical','open',0.93,'Deploy gating contradicts.'),
 -- already resolved, for the resolve-a-settled-conflict case
 ('00000000-0000-4000-8000-0000000c0004','00000000-0000-4000-8000-0000000000a0',
  '00000000-0000-4000-8000-00000000a004','00000000-0000-4000-8000-00000000b004',
  'contradiction','low','resolved',0.80,'Settled earlier.');
