import { Body, Controller, Get, Post, UseGuards } from '@nestjs/common';
import { WorkspacesService } from './workspaces.service';
import { JwtAuthGuard } from '../common/guards/jwt-auth.guard';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import { CreateWorkspaceDto } from './dto/create-workspace.dto';

@UseGuards(JwtAuthGuard)
@Controller('workspaces')
export class WorkspacesController {
  constructor(private readonly workspacesService: WorkspacesService) {}

  @Post()
  create(@Body() body: CreateWorkspaceDto, @CurrentUser() user: any) {
    return this.workspacesService.create(body.name, user.id);
  }

  @Get()
  list(@CurrentUser() user: any) {
    return this.workspacesService.listForUser(user.id);
  }
}
