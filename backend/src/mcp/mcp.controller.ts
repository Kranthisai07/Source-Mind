import { Body, Controller, Get, Param, Post, UseGuards } from '@nestjs/common';
import { McpService } from './mcp.service';
import { JwtAuthGuard } from '../common/guards/jwt-auth.guard';
import { CurrentUser } from '../common/decorators/current-user.decorator';
import * as manifest from './manifest.json';

@Controller('mcp')
export class McpController {
  constructor(private readonly mcpService: McpService) {}

  @Get('manifest')
  getManifest() {
    return manifest;
  }

  @UseGuards(JwtAuthGuard)
  @Post('tools/:tool')
  execTool(
    @Param('tool') tool: string,
    @Body() body: any,
    @CurrentUser() user: any,
  ) {
    return this.mcpService.exec(tool, body, user);
  }
}
