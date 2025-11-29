import { Module } from '@nestjs/common';
import { McpController } from './mcp.controller';
import { McpService } from './mcp.service';
import { MemoriesModule } from '../memories/memories.module';
import { RelationsModule } from '../relations/relations.module';
import { AttributionModule } from '../attribution/attribution.module';
import { HandoffModule } from '../handoff/handoff.module';
import { ConversationsModule } from '../conversations/conversations.module';
import { AccessControlModule } from '../access-control/access-control.module';

@Module({
  imports: [
    MemoriesModule,
    RelationsModule,
    AttributionModule,
    HandoffModule,
    ConversationsModule,
    AccessControlModule,
  ],
  controllers: [McpController],
  providers: [McpService],
})
export class McpModule {}
