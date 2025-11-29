import { Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { AccessControlService } from '../access-control/access-control.service';
import { AccessLevel } from '@prisma/client';

@Injectable()
export class RelationsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly accessControl: AccessControlService,
  ) {}

  async createRelation(
    workspaceId: string,
    fromMemoryId: string,
    toMemoryId: string,
    relationType: any,
    userId: string,
  ) {
    await this.accessControl.assertAccess({
      userId,
      workspaceId,
      memoryId: fromMemoryId,
      required: AccessLevel.write,
    });

    const from = await this.prisma.memory.findUnique({ where: { id: fromMemoryId } });
    const to = await this.prisma.memory.findUnique({ where: { id: toMemoryId } });
    if (!from || !to || from.workspaceId !== workspaceId || to.workspaceId !== workspaceId) {
      throw new NotFoundException('Memory not found');
    }

    return this.prisma.memoryRelation.create({
      data: { fromMemoryId, toMemoryId, relationType },
    });
  }

  async listRelations(memoryId: string) {
    return this.prisma.memoryRelation.findMany({
      where: { OR: [{ fromMemoryId: memoryId }, { toMemoryId: memoryId }] },
    });
  }
}
