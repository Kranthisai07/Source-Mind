import { IsEnum, IsNotEmpty, IsOptional, IsString } from 'class-validator';
import { MemorySource, MemoryType } from '@prisma/client';

export class CreateMemoryDto {
  @IsOptional()
  @IsString()
  projectId?: string;

  @IsEnum(MemoryType)
  type!: MemoryType;

  @IsEnum(MemorySource)
  source!: MemorySource;

  @IsOptional()
  @IsString()
  title?: string;

  @IsNotEmpty()
  content!: string;

  @IsOptional()
  metadata?: Record<string, any>;

  @IsOptional()
  aiProviderName?: string;

  @IsOptional()
  relations?: { toMemoryId: string; relationType: string }[];
}
