import { IsEnum, IsNotEmpty, IsString } from 'class-validator';
import { MemoryRelationType } from '@prisma/client';

export class CreateRelationDto {
  @IsNotEmpty()
  @IsString()
  toMemoryId!: string;

  @IsEnum(MemoryRelationType)
  relationType!: MemoryRelationType;
}
