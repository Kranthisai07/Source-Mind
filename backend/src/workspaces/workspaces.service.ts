import { Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { WorkspaceMemberRole } from '@prisma/client';

@Injectable()
export class WorkspacesService {
  constructor(private readonly prisma: PrismaService) {}

  async create(name: string, userId: string) {
    const workspace = await this.prisma.workspace.create({
      data: {
        name,
        members: {
          create: {
            userId,
            role: WorkspaceMemberRole.owner,
          },
        },
      },
      include: { members: true },
    });
    return workspace;
  }

  async listForUser(userId: string) {
    return this.prisma.workspace.findMany({
      where: { members: { some: { userId } } },
    });
  }

  async assertMembership(workspaceId: string, userId: string) {
    const membership = await this.prisma.workspaceMember.findFirst({
      where: { workspaceId, userId },
    });
    if (!membership) {
      throw new NotFoundException('Workspace not found or no access');
    }
    return membership;
  }
}
