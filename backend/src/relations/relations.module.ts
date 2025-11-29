import { Module } from '@nestjs/common';
import { RelationsService } from './relations.service';
import { RelationsController } from './relations.controller';
import { PrismaModule } from '../prisma/prisma.module';
import { AccessControlModule } from '../access-control/access-control.module';
import { WorkspacesModule } from '../workspaces/workspaces.module';

@Module({
  imports: [PrismaModule, AccessControlModule, WorkspacesModule],
  providers: [RelationsService],
  controllers: [RelationsController],
})
export class RelationsModule {}
