import { IsNotEmpty, IsOptional, IsString } from 'class-validator';

export class UpdateMemoryDto {
  @IsNotEmpty()
  @IsString()
  newContent!: string;

  @IsOptional()
  metadata?: Record<string, any>;
}
