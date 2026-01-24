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
                contributionPercent: attribution.contributionPercent,
                notes: attribution.notes,
              },
            ],
          }
          : undefined,
        edits: {
          create: {
            editorType:
              params.source === MemorySource.ai ? 'ai' : ('human' as EditHistory['editorType']),
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
    contributionPercent: number;
    notes?: string;
  } | null {
    if (params.source === MemorySource.human && params.authorUserId) {
      return {
        contributorType: 'human',
        contributorId: params.authorUserId,
        contributionPercent: 1,
      };
    }
    if (params.source === MemorySource.ai) {
      return {
        contributorType: 'ai',
        contributorId: params.aiProviderName ?? 'ai_provider',
        contributionPercent: 1,
      };
    }
    if (params.source === MemorySource.human_ai_mixed && params.authorUserId) {
      return {
        contributorType: 'human',
        contributorId: params.authorUserId,
        contributionPercent: 0.5,
        notes: 'Mixed creation, default split',
      };
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
    // For now we assume if content changed we re-embed, otherwise we might need to fetch raw
    const embedding = contentChanged
      ? await this.embeddingProvider.embedText(newContent)
      : null; // Can't easily reuse existing embedding with Unsupported type without raw query

    const deltaSummary = contentChanged
      ? await this.summarizeDelta(memory.content, newContent)
      : 'No content change';

    const updated = await this.prisma.memory.update({
      where: { id: memoryId },
      data: {
        content: newContent,
        // embedding handled separately
        metadata: metadata ?? memory.metadata,
        edits: {
          create: {
            editorType: 'human',
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
    }
    if (contentChanged) {
      await this.rebalanceAttribution(memoryId, userId, memory);
    }
    return updated;
  }

  private async rebalanceAttribution(memoryId: string, editorId: string, memory: Memory) {
    const attributions = await this.prisma.attribution.findMany({
      where: { memoryId },
    });
    if (!attributions.length) {
      await this.prisma.attribution.create({
        data: {
          memoryId,
          contributorType: 'human',
          contributorId: editorId,
          contributionPercent: 1,
          notes: 'Inferred after edit',
        },
      });
      return;
    }
    // Simple rule: move 20% attribution to editor (human)
    const editorAttr = attributions.find(
      (a) => a.contributorId === editorId && a.contributorType === 'human',
    );
    const newPercent = Math.min(1, (editorAttr?.contributionPercent ?? 0) + 0.2);
    if (editorAttr) {
      await this.prisma.attribution.update({
        where: { id: editorAttr.id },
        data: { contributionPercent: newPercent },
      });
    } else {
      await this.prisma.attribution.create({
        data: {
          memoryId,
          contributorType: 'human',
          contributorId: editorId,
          contributionPercent: 0.2,
          notes: 'Edit contribution',
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
  ) {
    const memories = await this.prisma.memory.findMany({
      where: {
        workspaceId,
        projectId: projectId ?? null,
        isDeleted: false,
      },
      orderBy: { createdAt: 'desc' },
      include: { attributions: true, edits: { take: 3, orderBy: { createdAt: 'desc' } } },
    });

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
