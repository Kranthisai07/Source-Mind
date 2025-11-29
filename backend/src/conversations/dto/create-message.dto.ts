import { ConversationRole } from '@prisma/client';
import { IsEnum, IsNotEmpty } from 'class-validator';

export class CreateMessageDto {
  @IsEnum(ConversationRole)
  role!: ConversationRole;

  @IsNotEmpty()
  content!: string;
}
