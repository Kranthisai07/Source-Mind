import { Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { LlmProvider } from '../llm/llm.provider';
import { MemoriesService } from '../memories/memories.service';
import { MemorySource, MemoryType } from '@prisma/client';
import { AccessControlService } from '../access-control/access-control.service';
import { AccessLevel, AccessRole } from '@prisma/client';

@Injectable()
export class HandoffService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly llm: LlmProvider,
    private readonly memories: MemoriesService,
    private readonly accessControl: AccessControlService,
  ) {}

  async handoff(workspaceId: string, fromUserId: string, toUserId: string) {
    const toUserMember = await this.prisma.workspaceMember.findFirst({
      where: { workspaceId, userId: toUserId },
    });
    if (!toUserMember) throw new NotFoundException('Recipient not in workspace');

    const contributions = await this.prisma.attribution.findMany({
      where: { contributorId: fromUserId, contributorType: 'human', memory: { workspaceId } },
      include: { memory: true },
    });
    const memoriesByProject: Record<string, typeof contributions> = {};
    for (const attr of contributions) {
      const pid = attr.memory.projectId || 'unassigned';
      memoriesByProject[pid] = memoriesByProject[pid] || [];
      memoriesByProject[pid].push(attr);
    }

    const createdSummaries = [];
    for (const [projectId, attrs] of Object.entries(memoriesByProject)) {
      const content = attrs
        .slice(0, 10)
        .map(
          (a) =>
            `Memory: ${a.memory.title ?? 'Untitled'}\nContent: ${a.memory.content}\nContribution: ${
              a.contributionPercent
            }`,
        )
        .join('\n\n');
      const summary = await this.llm.generate({
        prompt: `Summarize the key ownership details for handoff:\n${content}`,
        maxTokens: 200,
      });

      const memory = await this.memories.create({
        workspaceId,
        projectId: projectId === 'unassigned' ? undefined : projectId,
        authorUserId: toUserId,
        type: MemoryType.handoff_summary,
        source: MemorySource.human,
        title: 'Knowledge Handoff',
        content: summary,
        metadata: { fromUserId, sourceMemories: attrs.map((a) => a.memoryId) },
      });

      await this.accessControl.upsertRule(workspaceId, {
        projectId: projectId === 'unassigned' ? undefined : projectId,
        memoryId: memory.id,
        role: AccessRole.member,
        accessLevel: AccessLevel.write,
      });

      createdSummaries.push(memory);
    }

    return { created: createdSummaries.length, summaries: createdSummaries };
  }
}
