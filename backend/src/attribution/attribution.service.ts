import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class AttributionService {
  constructor(private readonly prisma: PrismaService) {}

  async projectSummary(workspaceId: string, projectId: string) {
    const grouped = await this.prisma.attribution.groupBy({
      by: ['contributorId', 'contributorType'],
      where: {
        memory: { workspaceId, projectId },
      },
      _sum: { contributionPercent: true },
      _count: { _all: true },
    });

    // Map contributorId to user names if human
    const humanContributors = grouped
      .filter((g) => g.contributorType === 'human')
      .map((g) => g.contributorId);

    const users = await this.prisma.user.findMany({
      where: { id: { in: humanContributors } },
      select: { id: true, name: true, email: true },
    });

    const memories = await this.prisma.memory.findMany({
      where: { workspaceId, projectId, isDeleted: false },
      select: { metadata: true },
    });
    const tagCounts: Record<string, number> = {};
    memories.forEach((m) => {
      const tags = (m.metadata as any)?.tags ?? [];
      if (Array.isArray(tags)) {
        tags.forEach((t) => {
          tagCounts[t] = (tagCounts[t] ?? 0) + 1;
        });
      }
    });
    const topTopics = Object.entries(tagCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([tag, count]) => ({ tag, count }));

    const totalHuman = grouped
      .filter((g) => g.contributorType === 'human')
      .reduce((acc, g) => acc + (g._sum.contributionPercent ?? 0), 0);
    const totalAi = grouped
      .filter((g) => g.contributorType === 'ai')
      .reduce((acc, g) => acc + (g._sum.contributionPercent ?? 0), 0);

    return {
      contributors: grouped.map((g) => ({
        contributorId: g.contributorId,
        contributorType: g.contributorType,
        contributionPercent: g._sum.contributionPercent ?? 0,
        entries: g._count._all,
        user:
          g.contributorType === 'human'
            ? users.find((u) => u.id === g.contributorId)
            : null,
      })),
      totals: { human: totalHuman, ai: totalAi },
      topTopics,
    };
  }
}
