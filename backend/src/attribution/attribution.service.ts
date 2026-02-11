import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class AttributionService {
  constructor(private readonly prisma: PrismaService) { }

  async projectSummary(workspaceId: string, projectId: string) {
    const grouped = await this.prisma.attribution.groupBy({
      by: ['contributorId', 'contributorType'],
      where: {
        memory: { workspaceId, projectId },
      },
      _count: { _all: true },
      _sum: { contributionScore: true },
    });

    // Map contributorId to user names if user
    const userContributors = grouped
      .filter((g) => g.contributorType === 'user')
      .map((g) => g.contributorId);

    const users = await this.prisma.user.findMany({
      where: { id: { in: userContributors } },
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

    const totalUser = grouped
      .filter((g) => g.contributorType === 'user')
      .reduce((acc, g) => acc + (g._sum.contributionScore ?? 0), 0);
    const totalTool = grouped
      .filter((g) => g.contributorType === 'tool')
      .reduce((acc, g) => acc + (g._count._all ?? 0), 0);

    return {
      contributors: grouped.map((g) => ({
        contributorId: g.contributorId,
        contributorType: g.contributorType,
        uniqueContributions: g._count._all,
        contributionScore: g.contributorType === 'user' ? (g._sum.contributionScore ?? 0) : 0,
        user:
          g.contributorType === 'user'
            ? users.find((u) => u.id === g.contributorId)
            : null,
      })),
      totals: { user: totalUser, tool: totalTool },
      topTopics,
    };
  }
}
