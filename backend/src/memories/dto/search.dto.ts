import { IsNotEmpty, IsNumber, IsOptional, IsString } from 'class-validator';

export class SearchDto {
  @IsNotEmpty()
  @IsString()
  query!: string;

  @IsOptional()
  @IsString()
  projectId?: string;

  @IsOptional()
  @IsNumber()
  limit?: number;
}
