import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { envValidationSchema } from './config/validation';
import { PrismaModule } from './prisma/prisma.module';
import { AuthModule } from './auth/auth.module';
import { UsersModule } from './users/users.module';
import { WorkspacesModule } from './workspaces/workspaces.module';
import { ProjectsModule } from './projects/projects.module';
import { MemoriesModule } from './memories/memories.module';
import { RelationsModule } from './relations/relations.module';
import { AttributionModule } from './attribution/attribution.module';
import { ConversationsModule } from './conversations/conversations.module';
import { AccessControlModule } from './access-control/access-control.module';
import { AnalyticsModule } from './analytics/analytics.module';
import { LlmModule } from './llm/llm.module';
import { HandoffModule } from './handoff/handoff.module';
import { McpModule } from './mcp/mcp.module';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      validationSchema: envValidationSchema,
    }),
    PrismaModule,
    LlmModule,
    AuthModule,
    UsersModule,
    WorkspacesModule,
    ProjectsModule,
    MemoriesModule,
    RelationsModule,
    AttributionModule,
    ConversationsModule,
    AccessControlModule,
    AnalyticsModule,
    HandoffModule,
    McpModule,
  ],
})
export class AppModule {}
