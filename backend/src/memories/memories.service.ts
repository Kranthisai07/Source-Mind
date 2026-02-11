import { Injectable, NotFoundException } from '@nestjs/common';
import {
  AccessLevel,
  Attribution,
  ContributorType,
  EditHistory,
  Memory,
  MemorySource,
  MemoryType,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { EmbeddingProvider } from '../llm/embedding.provider';
import { computeImportanceScore } from './utils/importance-score';
import { AccessControlService } from '../access-control/access-control.service';
import { LlmProvider } from '../llm/llm.provider';

interface CreateMemoryParams {
  workspaceId: string;
  projectId?: string;
  authorUserId?: string;
  type: MemoryType;
  source: MemorySource;
  title?: string;
  content: string;
  metadata?: any;
  aiProviderName?: string;
}

@Injectable()
export class MemoriesService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly embeddingProvider: EmbeddingProvider,
    private readonly accessControl: AccessControlService,
    private readonly llmProvider: LlmProvider,
  ) { }

  async create(params: CreateMemoryParams): Promise<Memory> {
    const embedding = await this.embeddingProvider.embedText(params.content);
    const importanceScore = computeImportanceScore(params.content);
    const attribution = this.buildAttribution(params);

    const memory = await this.prisma.memory.create({
      data: {
        workspaceId: params.workspaceId,
        projectId: params.projectId,
        authorUserId: params.authorUserId,
        type: params.type,
        source: params.source,
        title: params.title,
        content: params.content,
        // embedding handled separately due to Unsupported type
        importanceScore,
        metadata: params.metadata,
        attributions: attribution
          ? {
            create: [
              {
                contributorType: attribution.contributorType,
                contributorId: attribution.contributorId,
                contributionScore: attribution.contributorType === 'user' ? 1.0 : null,
                notes: attribution.notes,
              },
            ],
          }
          : undefined,
        edits: {
          create: {
            editorType:
              params.source === MemorySource.ai ? 'tool' : ('user' as EditHistory['editorType']),
            editorId: params.authorUserId ?? params.aiProviderName ?? 'system',
            deltaSummary: 'Initial version',
            previousContent: null,
            newContent: params.content,
          },
        },
      },
    });

    // Update embedding
    if (embedding) {
      const vectorString = `[${embedding.join(',')}]`;
      await this.prisma.$executeRawUnsafe(
        `UPDATE "Memory" SET embedding = $1::vector WHERE id = $2`,
        vectorString,
        memory.id,
      );
    }

    // create relations if provided in metadata
    if ((params as any).relations?.length) {
      await this.prisma.memoryRelation.createMany({
        data: (params as any).relations.map((r: any) => ({
          fromMemoryId: memory.id,
          toMemoryId: r.toMemoryId,
          relationType: r.relationType,
        })),
      });
    }

    return memory;
  }

  private buildAttribution(params: CreateMemoryParams): {
    contributorType: Attribution['contributorType'];
    contributorId: string;
    notes?: string;
  } | null {
    if (params.source === MemorySource.human && params.authorUserId) {
      return {
        contributorType: 'user',
        contributorId: params.authorUserId,
      };
    }
    if (params.source === MemorySource.ai) {
      return {
        contributorType: 'tool',
        contributorId: params.aiProviderName ?? 'ai_provider',
      };
    }
    if (params.source === MemorySource.external_tool && params.authorUserId) {
      return {
        contributorType: 'user',
        contributorId: params.authorUserId,
        notes: 'Imported from external tool'
      }
    }
    return null;
  }

  async updateContent(
    workspaceId: string,
    memoryId: string,
    userId: string,
    newContent: string,
    metadata?: any,
  ) {
    await this.accessControl.assertAccess({
      userId,
      workspaceId,
      memoryId,
      required: AccessLevel.write,
    });

    const memory = await this.prisma.memory.findUnique({ where: { id: memoryId } });
    if (!memory || memory.workspaceId !== workspaceId) {
      throw new NotFoundException('Memory not found');
    }

    const contentChanged = newContent !== memory.content;
    const embedding = contentChanged
      ? await this.embeddingProvider.embedText(newContent)
      : null;

    const deltaSummary = contentChanged
      ? await this.summarizeDelta(memory.content, newContent)
      : 'No content change';

    const updated = await this.prisma.memory.update({
      where: { id: memoryId },
      data: {
        content: newContent,
        metadata: metadata ?? memory.metadata,
        edits: {
          create: {
            editorType: 'user',
            editorId: userId,
            deltaSummary,
            previousContent: memory.content,
            newContent,
          },
        },
      },
    });

    if (contentChanged) {
      const vectorString = `[${(embedding as number[]).join(',')}]`;
      await this.prisma.$executeRawUnsafe(
        `UPDATE "Memory" SET embedding = $1::vector WHERE id = $2`,
        vectorString,
        memoryId,
      );

      await this.rebalanceHumanAttribution(memoryId);
    }
    return updated;
  }

  private async rebalanceHumanAttribution(memoryId: string) {
    const edits = await this.prisma.editHistory.findMany({
      where: { memoryId },
      orderBy: { createdAt: 'asc' },
    });
    const memory = await this.prisma.memory.findUnique({ where: { id: memoryId } });

    if (!memory) return;

    let userScores: Record<string, number> = {};
    let totalScore = 0;

    // Use a simplified heuristic: Base points for author + points per edit weighted by change size
    // Initial Author
    if (memory.authorUserId && memory.source === 'human') {
      const initialPoints = 100; // Base score for creating
      userScores[memory.authorUserId] = initialPoints;
      totalScore += initialPoints;
    }

    // Process Edits
    for (const edit of edits) {
      if (edit.editorType === 'user') {
        const delta = Math.abs(edit.newContent.length - (edit.previousContent?.length || 0));
        const points = 10 + (delta / 10); // 10 points per edit + 1 point per 10 chars changed
        userScores[edit.editorId] = (userScores[edit.editorId] || 0) + points;
        totalScore += points;
      }
      // Tools ignored for score
    }

    // Verify consistency: If no user edits/author, totalScore is 0.
    if (totalScore === 0) return;

    // Upsert Attributions
    for (const [userId, points] of Object.entries(userScores)) {
      const score = points / totalScore;
      await this.prisma.attribution.upsert({
        where: {
          attribution_unique_idx: {
            memoryId,
            contributorType: 'user',
            contributorId: userId,
          },
        },
        update: { contributionScore: score },
        create: {
          memoryId,
          contributorType: 'user',
          contributorId: userId,
          contributionScore: score,
        },
      });
    }

    // Also ensure tool editors are listed (with null score)
    const toolEditors = new Set(edits.filter(e => e.editorType === 'tool').map(e => e.editorId));
    if (memory.source === 'ai' && memory.authorUserId) toolEditors.add(memory.authorUserId); // memory.authorUserId stores provider name for AI

    for (const toolId of toolEditors) {
      await this.prisma.attribution.upsert({
        where: {
          attribution_unique_idx: {
            memoryId,
            contributorType: 'tool',
            contributorId: toolId,
          },
        },
        update: { contributionScore: null },
        create: {
          memoryId,
          contributorType: 'tool',
          contributorId: toolId,
          contributionScore: null,
        },
      });
    }
  }

  async search(
    workspaceId: string,
    userId: string,
    query: string,
    projectId?: string,
    limit = 5,
  ) {
    const vector = await this.embeddingProvider.embedText(query);
    const vectorLiteral = `[${vector.join(',')}]`;
    const params: any[] = [vectorLiteral, workspaceId];
    let sql = `
      SELECT m.*, 1 - (m.embedding <=> $1::vector) as score
      FROM "Memory" m
      WHERE m."workspaceId" = $2 AND m."isDeleted" = false
    `;
    if (projectId) {
      params.push(projectId);
      sql += ` AND m."projectId" = $${params.length}`;
    }
    params.push(limit);
    sql += ` ORDER BY m.embedding <=> $1::vector LIMIT $${params.length}`;

    const results = await this.prisma.$queryRawUnsafe<Array<Memory & { score: number }>>(
      sql,
      ...params,
    );

    const filtered = [];
    for (const mem of results) {
      try {
        await this.accessControl.assertAccess({
          userId,
          workspaceId,
          projectId: mem.projectId,
          memoryId: mem.id,
          required: AccessLevel.read,
        });
        filtered.push(mem);
      } catch {
        // ignore
      }
    }

    // Attach related memory ids
    const withRelations = await Promise.all(
      filtered.map(async (mem) => {
        const relations = await this.prisma.memoryRelation.findMany({
          where: { OR: [{ fromMemoryId: mem.id }, { toMemoryId: mem.id }] },
        });
        const relatedIds = relations.map((r) =>
          r.fromMemoryId === mem.id ? r.toMemoryId : r.fromMemoryId,
        );
        return { memory: mem, score: (mem as any).score ?? 0, relatedMemoryIds: relatedIds };
      }),
    );

    return withRelations;
  }

  async listByProject(
    workspaceId: string,
    userId: string,
    projectId?: string | null,
    page: number = 1,
    limit: number = 50,
  ) {
    const skip = (page - 1) * limit;

    const memories = await this.prisma.memory.findMany({
      where: {
        workspaceId,
        projectId: projectId ?? null,
        isDeleted: false,
      },
      orderBy: { createdAt: 'desc' },
      include: { attributions: true, edits: { take: 3, orderBy: { createdAt: 'desc' } } },
      skip,
      take: limit,
    });

    // Access control filtering (Note: This might result in fewer than 'limit' items if some are hidden)
    // For MVP, we assume project membership grants read access to all memories in it.
    const filtered = [];
    for (const mem of memories) {
      try {
        await this.accessControl.assertAccess({
          userId,
          workspaceId,
          projectId: mem.projectId,
          memoryId: mem.id,
          required: AccessLevel.read,
        });
        filtered.push(mem);
      } catch {
        // skip
      }
    }
    return filtered;
  }

  private async summarizeDelta(oldContent: string, newContent: string): Promise<string> {
    if (!this.llmProvider) return 'Edited content';
    const prompt = `Summarize the change between original and new text in one sentence.\n\nOriginal:\n${oldContent}\n\nNew:\n${newContent}`;
    try {
      return await this.llmProvider.generate({ prompt, maxTokens: 50 });
    } catch {
      return 'Edited content';
    }
  }
}
