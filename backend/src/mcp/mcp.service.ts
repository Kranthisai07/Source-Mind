import { Injectable, UnauthorizedException } from '@nestjs/common';
import { MemoriesService } from '../memories/memories.service';
import { RelationsService } from '../relations/relations.service';
import { AttributionService } from '../attribution/attribution.service';
import { HandoffService } from '../handoff/handoff.service';
import { ConversationsService } from '../conversations/conversations.service';
import { AccessControlService } from '../access-control/access-control.service';
import { AccessLevel } from '@prisma/client';

@Injectable()
export class McpService {
  constructor(
    private readonly memories: MemoriesService,
    private readonly relations: RelationsService,
    private readonly attribution: AttributionService,
    private readonly handoff: HandoffService,
    private readonly conversations: ConversationsService,
    private readonly access: AccessControlService,
  ) {}

  async exec(tool: string, params: any, user: any) {
    switch (tool) {
      case 'memory.search':
        await this.access.assertAccess({
          userId: user.id,
          workspaceId: params.workspaceId,
          projectId: params.projectId,
          required: AccessLevel.read,
        });
        return this.memories.search(
          params.workspaceId,
          user.id,
          params.query,
          params.projectId,
          params.limit ?? 5,
        );
      case 'memory.add':
        await this.access.assertAccess({
          userId: user.id,
          workspaceId: params.workspaceId,
          projectId: params.projectId,
          required: AccessLevel.write,
        });
        return this.memories.create({
          workspaceId: params.workspaceId,
          projectId: params.projectId,
          authorUserId: user.id,
          type: params.type,
          source: params.source,
          title: params.title,
          content: params.content,
          metadata: params.metadata,
          aiProviderName: params.aiProviderName,
        });
      case 'memory.update':
        await this.access.assertAccess({
          userId: user.id,
          workspaceId: params.workspaceId,
          memoryId: params.memoryId,
          required: AccessLevel.write,
        });
        return this.memories.updateContent(
          params.workspaceId,
          params.memoryId,
          user.id,
          params.newContent,
          params.metadata,
        );
      case 'memory.relations':
        return this.relations.listRelations(params.memoryId);
      case 'memory.contextForChat':
        return this.memories.search(
          params.workspaceId,
          user.id,
          params.query,
          params.projectId,
          params.limit ?? 5,
        );
      case 'handoff.generate':
        return this.handoff.handoff(params.workspaceId, params.fromUserId, params.toUserId);
      case 'attribution.summary':
        return this.attribution.projectSummary(params.workspaceId, params.projectId);
      default:
        throw new UnauthorizedException('Unknown tool');
    }
  }
}
