import { Body, Controller, Param, Post, UseGuards } from '@nestjs/common';
import { RelationsService } from './relations.service';
import { JwtAuthGuard } from '../common/guards/jwt-auth.guard';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { CreateRelationDto } from './dto/create-relation.dto';
import { WorkspacesService } from '../workspaces/workspaces.service';
import { Get } from '@nestjs/common';

@UseGuards(JwtAuthGuard)
@Controller('workspaces/:wid/memories/:mid/relations')
export class RelationsController {
  constructor(
    private readonly relationsService: RelationsService,
    private readonly workspacesService: WorkspacesService,
  ) {}

  @Post()
  async create(
    @Param('wid') workspaceId: string,
    @Param('mid') memoryId: string,
    @Body() body: CreateRelationDto,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    return this.relationsService.createRelation(
      workspaceId,
      memoryId,
      body.toMemoryId,
      body.relationType,
      user.id,
    );
  }

  @Get()
  async list(
    @Param('wid') workspaceId: string,
    @Param('mid') memoryId: string,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    return this.relationsService.listRelations(memoryId);
  }
}
