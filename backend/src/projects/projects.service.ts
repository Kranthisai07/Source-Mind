import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { WorkspacesService } from '../workspaces/workspaces.service';

@Injectable()
export class ProjectsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly workspacesService: WorkspacesService,
  ) {}

  async create(workspaceId: string, name: string, description: string | null) {
    return this.prisma.project.create({
      data: { workspaceId, name, description },
    });
  }

  async list(workspaceId: string) {
    return this.prisma.project.findMany({ where: { workspaceId } });
  }
}
