import { Module } from '@nestjs/common';
import { AttributionService } from './attribution.service';
import { AnalyticsController } from './analytics.controller';
import { PrismaModule } from '../prisma/prisma.module';
import { WorkspacesModule } from '../workspaces/workspaces.module';
import { AccessControlModule } from '../access-control/access-control.module';

@Module({
  imports: [PrismaModule, WorkspacesModule, AccessControlModule],
  providers: [AttributionService],
  controllers: [AnalyticsController],
  exports: [AttributionService],
})
export class AttributionModule {}
