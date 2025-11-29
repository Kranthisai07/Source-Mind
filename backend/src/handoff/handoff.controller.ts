import { Body, Controller, Param, Post, UseGuards } from '@nestjs/common';
import { HandoffService } from './handoff.service';
import { JwtAuthGuard } from '../common/guards/jwt-auth.guard';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { WorkspacesService } from '../workspaces/workspaces.service';

@UseGuards(JwtAuthGuard)
@Controller('workspaces/:wid/knowledge-handoff')
export class HandoffController {
  constructor(
    private readonly handoffService: HandoffService,
    private readonly workspacesService: WorkspacesService,
  ) {}

  @Post()
  async handoff(
    @Param('wid') workspaceId: string,
    @Body() body: { fromUserId: string; toUserId: string },
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    return this.handoffService.handoff(workspaceId, body.fromUserId, body.toUserId);
  }
}
