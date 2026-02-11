import {
  Body,
  Controller,
  Param,
  Patch,
  Post,
  UseGuards,
} from '@nestjs/common';
import { MemoriesService } from './memories.service';
import { JwtAuthGuard } from '../common/guards/jwt-auth.guard';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { CreateMemoryDto } from './dto/create-memory.dto';
import { WorkspacesService } from '../workspaces/workspaces.service';
import { UpdateMemoryDto } from './dto/update-memory.dto';
import { SearchDto } from './dto/search.dto';
import { ListMemoriesDto } from './dto/list-memories.dto';
import { AccessControlService } from '../access-control/access-control.service';
import { AccessLevel } from '@prisma/client';

@UseGuards(JwtAuthGuard)
@Controller()
export class MemoriesController {
  constructor(
    private readonly memoriesService: MemoriesService,
    private readonly workspacesService: WorkspacesService,
    private readonly accessControl: AccessControlService,
  ) { }

  @Post('workspaces/:wid/memories')
  async create(
    @Param('wid') workspaceId: string,
    @Body() body: CreateMemoryDto,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    await this.accessControl.assertAccess({
      userId: user.id,
      workspaceId,
      projectId: body.projectId,
      required: AccessLevel.write,
    });

    return this.memoriesService.create({
      workspaceId,
      projectId: body.projectId,
      authorUserId: user.id,
      type: body.type,
      source: body.source,
      title: body.title,
      content: body.content,
      metadata: body.metadata,
      aiProviderName: body.aiProviderName,
    });
  }

  @Patch('workspaces/:wid/memories/:id')
  async update(
    @Param('wid') workspaceId: string,
    @Param('id') memoryId: string,
    @Body() body: UpdateMemoryDto,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    return this.memoriesService.updateContent(
      workspaceId,
      memoryId,
      user.id,
      body.newContent,
      body.metadata,
    );
  }

  @Post('workspaces/:wid/search')
  async search(
    @Param('wid') workspaceId: string,
    @Body() body: SearchDto,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    return this.memoriesService.search(
      workspaceId,
      user.id,
      body.query,
      body.projectId,
      body.limit ?? 5,
    );
  }

  @Post('workspaces/:wid/projects/:pid/memories/list')
  async listByProject(
    @Param('wid') workspaceId: string,
    @Param('pid') projectId: string,
    @Body() body: ListMemoriesDto,
    @CurrentUser() user: any,
  ) {
    await this.workspacesService.assertMembership(workspaceId, user.id);
    return this.memoriesService.listByProject(workspaceId, user.id, projectId, body.page, body.limit);
  }
}
