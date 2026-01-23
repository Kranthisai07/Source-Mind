import { Body, Controller, Get, Param, Post, UseGuards } from '@nestjs/common';
import { ProjectsService } from './projects.service';
import { JwtAuthGuard } from '../common/guards/jwt-auth.guard';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { CreateProjectDto } from './dto/create-project.dto';
import { WorkspacesService } from '../workspaces/workspaces.service';

@UseGuards(JwtAuthGuard)
@Controller('workspaces/:wid/projects')
export class ProjectsController {
  constructor(
    private readonly projectsService: ProjectsService,
    private readonly workspacesService: WorkspacesService,
  ) {}

  @Post()
  async create(
    @Param('wid') workspaceId: string,
    @Body() body: CreateProjectDto,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    return this.projectsService.create(
      workspaceId,
      body.name,
      body.description ?? null,
    );
  }

  @Get()
  async list(
    @Param('wid') workspaceId: string,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    return this.projectsService.list(workspaceId);
  }
}
