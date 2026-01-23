import { Body, Controller, Param, Post, UseGuards } from '@nestjs/common';
import { ConversationsService } from './conversations.service';
import { JwtAuthGuard } from '../common/guards/jwt-auth.guard';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { CreateConversationDto } from './dto/create-conversation.dto';
import { CreateMessageDto } from './dto/create-message.dto';
import { WorkspacesService } from '../workspaces/workspaces.service';

@UseGuards(JwtAuthGuard)
@Controller('workspaces/:wid/conversations')
export class ConversationsController {
  constructor(
    private readonly conversationsService: ConversationsService,
    private readonly workspacesService: WorkspacesService,
  ) {}

  @Post()
  async createContext(
    @Param('wid') workspaceId: string,
    @Body() body: CreateConversationDto,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    return this.conversationsService.createContext(workspaceId, user.id, body);
  }

  @Post(':cid/messages')
  async addMessage(
    @Param('wid') workspaceId: string,
    @Param('cid') conversationId: string,
    @Body() body: CreateMessageDto,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    return this.conversationsService.addMessage(
      workspaceId,
      conversationId,
      user.id,
      body,
    );
  }
}
