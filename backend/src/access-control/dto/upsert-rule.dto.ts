import { IsEnum, IsOptional, IsString } from 'class-validator';
import { AccessLevel, AccessRole } from '@prisma/client';

export class UpsertRuleDto {
  @IsOptional()
  @IsString()
  projectId?: string;

  @IsOptional()
  @IsString()
  memoryId?: string;

  @IsEnum(AccessRole)
  role!: AccessRole;

  @IsEnum(AccessLevel)
  accessLevel!: AccessLevel;
}
