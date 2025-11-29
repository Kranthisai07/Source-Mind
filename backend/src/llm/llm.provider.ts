import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import OpenAI from 'openai';

export interface GenerateOptions {
  prompt: string;
  system?: string;
  maxTokens?: number;
  temperature?: number;
}

@Injectable()
export class LlmProvider {
  private readonly logger = new Logger(LlmProvider.name);
  private readonly client: OpenAI | null;
  private readonly model = 'gpt-4o-mini';

  constructor(private readonly config: ConfigService) {
    const apiKey = this.config.get<string>('OPENAI_API_KEY');
    this.client = apiKey ? new OpenAI({ apiKey }) : null;
  }

  async generate(options: GenerateOptions): Promise<string> {
    if (!this.client) {
      this.logger.warn('OPENAI_API_KEY missing; returning placeholder output');
      return 'LLM output unavailable (no API key configured).';
    }

    const messages: OpenAI.ChatCompletionMessageParam[] = [];
    if (options.system) {
      messages.push({ role: 'system', content: options.system });
    }
    messages.push({ role: 'user', content: options.prompt });

    const res = await this.client.chat.completions.create({
      model: this.model,
      messages,
      max_tokens: options.maxTokens ?? 256,
      temperature: options.temperature ?? 0.2,
    });
    return res.choices[0]?.message?.content ?? '';
  }
}
