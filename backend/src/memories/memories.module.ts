import { Module } from '@nestjs/common';
import { MemoriesController } from './memories.controller';
import { MemoriesService } from './memories.service';
import { PrismaModule } from '../prisma/prisma.module';
import { WorkspacesModule } from '../workspaces/workspaces.module';
import { LlmModule } from '../llm/llm.module';
import { AccessControlModule } from '../access-control/access-control.module';

@Module({
  imports: [PrismaModule, WorkspacesModule, LlmModule, AccessControlModule],
  controllers: [MemoriesController],
  providers: [MemoriesService],
  exports: [MemoriesService],
})
export class MemoriesModule {}
