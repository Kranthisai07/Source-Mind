import { Injectable, OnModuleInit, OnModuleDestroy, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PrismaClient } from '@prisma/client';

@Injectable()
export class PrismaService
  extends PrismaClient
  implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(PrismaService.name);

  constructor(private config: ConfigService) {
    super({
      datasources: {
        db: {
          url: config.get('DATABASE_URL'),
        },
      },
      log: ['info', 'warn', 'error'],
    });
  }

  async onModuleInit() {
    this.logger.log('Connecting to database...');
    try {
      await this.$connect();
      this.logger.log('Database connected successfully');
      // Ensure pgvector extension exists
      await this.$executeRawUnsafe('CREATE EXTENSION IF NOT EXISTS vector;');
    } catch (e) {
      this.logger.error('Failed to connect to database', e);
      throw e;
    }
  }

  async onModuleDestroy() {
    await this.$disconnect();
  }
}
