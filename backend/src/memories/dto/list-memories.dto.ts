import { IsNumber, IsOptional, Min } from 'class-validator';
import { Type } from 'class-transformer';

export class ListMemoriesDto {
    @IsOptional()
    @Type(() => Number)
    @IsNumber()
    @Min(1)
    page?: number;

    @IsOptional()
    @Type(() => Number)
    @IsNumber()
    @Min(1)
    limit?: number;
}
