import { IsNotEmpty, IsOptional, IsString } from 'class-validator';

export class CreateProjectDto {
  @IsNotEmpty()
  name!: string;

  @IsOptional()
  @IsString()
  description?: string;
}
