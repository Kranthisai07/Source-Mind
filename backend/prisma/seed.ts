import { PrismaClient, MemorySource, MemoryType, WorkspaceMemberRole } from '@prisma/client';
import * as bcrypt from 'bcrypt';

const prisma = new PrismaClient();

async function main() {
  const password = await bcrypt.hash('password123', 10);
  const user = await prisma.user.upsert({
    where: { email: 'demo@sourcemind.test' },
    update: {},
    create: {
      email: 'demo@sourcemind.test',
      name: 'Demo User',
      password,
    },
  });

  const workspace = await prisma.workspace.create({
    data: {
      name: 'Demo Workspace',
      members: {
        create: {
          userId: user.id,
          role: WorkspaceMemberRole.owner,
        },
      },
    },
  });

  const project = await prisma.project.create({
    data: {
      workspaceId: workspace.id,
      name: 'Demo Project',
      description: 'Sample project for SourceMind',
    },
  });

  const m1 = await prisma.memory.create({
    data: {
      workspaceId: workspace.id,
      projectId: project.id,
      authorUserId: user.id,
      type: MemoryType.decision,
      source: MemorySource.human,
      title: 'Choose OAuth 2.0',
      content: 'We should implement OAuth 2.0 for user authentication to support mobile clients.',
      // embedding handled separately due to Unsupported type
      importanceScore: 0.8,
      metadata: { tags: ['auth', 'security'] },
      attributions: {
        create: {
          contributorType: 'human',
          contributorId: user.id,
          contributionPercent: 1,
        },
      },
      edits: {
        create: {
          editorType: 'human',
          editorId: user.id,
          deltaSummary: 'Initial version',
          previousContent: null,
          newContent: 'We should implement OAuth 2.0 for user authentication to support mobile clients.',
        },
      },
    },
  });

  await prisma.$executeRawUnsafe(
    `UPDATE "Memory" SET embedding = $1::vector WHERE id = $2`,
    `[${Array(1536).fill(0).join(',')}]`,
    m1.id
  );

  const m2 = await prisma.memory.create({
    data: {
      workspaceId: workspace.id,
      projectId: project.id,
      authorUserId: user.id,
      type: MemoryType.note,
      source: MemorySource.human_ai_mixed,
      title: 'Token refresh strategy',
      content: 'Implement refresh tokens with 7-day expiry and rotate on use. AI suggested redis cache.',
      // embedding handled separately due to Unsupported type
      importanceScore: 0.6,
      metadata: { tags: ['auth', 'tokens'] },
      attributions: {
        createMany: {
          data: [
            { contributorType: 'human', contributorId: user.id, contributionPercent: 0.7 },
            { contributorType: 'ai', contributorId: 'openai', contributionPercent: 0.3 },
          ],
        },
      },
      edits: {
        create: {
          editorType: 'human',
          editorId: user.id,
          deltaSummary: 'Initial note',
          previousContent: null,
          newContent: 'Implement refresh tokens with 7-day expiry and rotate on use. AI suggested redis cache.',
        },
      },
    },
  });

  await prisma.$executeRawUnsafe(
    `UPDATE "Memory" SET embedding = $1::vector WHERE id = $2`,
    `[${Array(1536).fill(0).join(',')}]`,
    m2.id
  );

  await prisma.memoryRelation.create({
    data: {
      fromMemoryId: m2.id,
      toMemoryId: m1.id,
      relationType: 'updates',
    },
  });

  await prisma.conversationContext.create({
    data: {
      workspaceId: workspace.id,
      projectId: project.id,
      userId: user.id,
      messages: {
        create: [
          {
            role: 'user',
            content: 'Why OAuth?',
            metadata: { suggestedMemories: [] },
          },
        ],
      },
    },
  });

  console.log('Seeded demo user/workspace/project.');
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
