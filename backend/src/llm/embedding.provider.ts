import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import OpenAI from 'openai';

@Injectable()
export class EmbeddingProvider {
  private readonly logger = new Logger(EmbeddingProvider.name);
  private readonly client: OpenAI | null;
  private readonly model = 'text-embedding-3-small';

  constructor(private readonly config: ConfigService) {
    const apiKey = this.config.get<string>('OPENAI_API_KEY');
    this.client = apiKey ? new OpenAI({ apiKey }) : null;
  }

  async embedText(text: string): Promise<number[]> {
    if (!this.client) {
      this.logger.warn('OPENAI_API_KEY missing; returning zero vector placeholder');
      return Array(1536).fill(0);
    }
    const response = await this.client.embeddings.create({
      input: text,
      model: this.model,
    });
    return response.data[0].embedding;
  }
}
