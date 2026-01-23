import { Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { CreateConversationDto } from './dto/create-conversation.dto';
import { CreateMessageDto } from './dto/create-message.dto';
import { MemoriesService } from '../memories/memories.service';

@Injectable()
export class ConversationsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly memoriesService: MemoriesService,
  ) {}

  async createContext(
    workspaceId: string,
    userId: string,
    dto: CreateConversationDto,
  ) {
    return this.prisma.conversationContext.create({
      data: {
        workspaceId,
        projectId: dto.projectId,
        userId,
        externalSessionId: dto.externalSessionId,
      },
    });
  }

  async addMessage(
    workspaceId: string,
    conversationId: string,
    userId: string,
    dto: CreateMessageDto,
  ) {
    const convo = await this.prisma.conversationContext.findUnique({
      where: { id: conversationId },
    });
    if (!convo || convo.workspaceId !== workspaceId) {
      throw new NotFoundException('Conversation not found');
    }

    const message = await this.prisma.conversationMessage.create({
      data: {
        conversationContextId: conversationId,
        role: dto.role,
        content: dto.content,
      },
    });

    if (dto.role === 'user') {
      const context = await this.memoriesService.search(
        workspaceId,
        userId,
        dto.content,
        convo.projectId ?? undefined,
        5,
      );
      await this.prisma.conversationMessage.update({
        where: { id: message.id },
        data: {
          metadata: { suggestedMemories: context },
        },
      });
      return { message, assistantSuggestedContext: context };
    }

    return { message, assistantSuggestedContext: [] };
  }
}
