import { Controller, Get, Param, UseGuards } from '@nestjs/common';
import { AttributionService } from './attribution.service';
import { JwtAuthGuard } from '../common/guards/jwt-auth.guard';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { WorkspacesService } from '../workspaces/workspaces.service';
import { AccessControlService } from '../access-control/access-control.service';
import { AccessLevel } from '@prisma/client';

@UseGuards(JwtAuthGuard)
@Controller('workspaces/:wid/projects/:pid/attribution-summary')
export class AnalyticsController {
  constructor(
    private readonly attributionService: AttributionService,
    private readonly workspacesService: WorkspacesService,
    private readonly accessControl: AccessControlService,
  ) {}

  @Get()
  async summary(
    @Param('wid') workspaceId: string,
    @Param('pid') projectId: string,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    await this.accessControl.assertAccess({
      userId: user.id,
      workspaceId,
      projectId,
      required: AccessLevel.read,
    });
    return this.attributionService.projectSummary(workspaceId, projectId);
  }
}
