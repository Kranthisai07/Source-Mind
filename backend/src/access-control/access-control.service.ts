import {
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import {
  AccessControlRule,
  AccessLevel,
  AccessRole,
  WorkspaceMemberRole,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

const ACCESS_ORDER: Record<AccessLevel, number> = {
  none: 0,
  summary_only: 1,
  read: 2,
  write: 3,
  admin: 4,
};

const DEFAULT_ACCESS_BY_ROLE: Record<WorkspaceMemberRole, AccessLevel> = {
  owner: AccessLevel.admin,
  admin: AccessLevel.admin,
  member: AccessLevel.read,
};

@Injectable()
export class AccessControlService {
  constructor(private readonly prisma: PrismaService) {}

  async upsertRule(
    workspaceId: string,
    dto: {
      projectId?: string;
      memoryId?: string;
      role: AccessRole;
      accessLevel: AccessLevel;
    },
  ): Promise<AccessControlRule> {
    const existing = await this.prisma.accessControlRule.findFirst({
      where: {
        workspaceId,
        projectId: dto.projectId ?? null,
        memoryId: dto.memoryId ?? null,
        role: dto.role,
      },
    });

    if (existing) {
      return this.prisma.accessControlRule.update({
        where: { id: existing.id },
        data: { accessLevel: dto.accessLevel },
      });
    }

    return this.prisma.accessControlRule.create({
      data: {
        workspaceId,
        projectId: dto.projectId,
        memoryId: dto.memoryId,
        role: dto.role,
        accessLevel: dto.accessLevel,
      },
    });
  }

  async assertAccess(options: {
    userId: string;
    workspaceId: string;
    projectId?: string | null;
    memoryId?: string | null;
    required: AccessLevel;
  }) {
    const level = await this.getEffectiveAccess(
      options.userId,
      options.workspaceId,
      options.projectId,
      options.memoryId,
    );
    if (ACCESS_ORDER[level] < ACCESS_ORDER[options.required]) {
      throw new ForbiddenException('Insufficient permissions');
    }
  }

  async getEffectiveAccess(
    userId: string,
    workspaceId: string,
    projectId?: string | null,
    memoryId?: string | null,
  ): Promise<AccessLevel> {
    const membership = await this.prisma.workspaceMember.findFirst({
      where: { workspaceId, userId },
    });
    if (!membership) {
      throw new NotFoundException('Not a workspace member');
    }

    // Check specific memory rule
    if (memoryId) {
      const rule = await this.prisma.accessControlRule.findFirst({
        where: { workspaceId, memoryId, role: mapMemberToAccessRole(membership.role) },
      });
      if (rule) return rule.accessLevel;
    }

    // Check project rule
    if (projectId) {
      const rule = await this.prisma.accessControlRule.findFirst({
        where: { workspaceId, projectId, role: mapMemberToAccessRole(membership.role) },
      });
      if (rule) return rule.accessLevel;
    }

    // Workspace rule
    const workspaceRule = await this.prisma.accessControlRule.findFirst({
      where: { workspaceId, projectId: null, memoryId: null, role: mapMemberToAccessRole(membership.role) },
    });
    if (workspaceRule) return workspaceRule.accessLevel;

    return DEFAULT_ACCESS_BY_ROLE[membership.role] ?? AccessLevel.read;
  }
}

function mapMemberToAccessRole(role: WorkspaceMemberRole): AccessRole {
  if (role === WorkspaceMemberRole.owner) return AccessRole.owner;
  if (role === WorkspaceMemberRole.admin) return AccessRole.admin;
  return AccessRole.member;
}
