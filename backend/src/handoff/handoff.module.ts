import { Module } from '@nestjs/common';
import { HandoffService } from './handoff.service';
import { HandoffController } from './handoff.controller';
import { PrismaModule } from '../prisma/prisma.module';
import { LlmModule } from '../llm/llm.module';
import { MemoriesModule } from '../memories/memories.module';
import { AccessControlModule } from '../access-control/access-control.module';
import { WorkspacesModule } from '../workspaces/workspaces.module';

@Module({
  imports: [PrismaModule, LlmModule, MemoriesModule, AccessControlModule, WorkspacesModule],
  providers: [HandoffService],
  controllers: [HandoffController],
  exports: [HandoffService],
})
export class HandoffModule {}
