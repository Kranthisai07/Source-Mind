import { Module } from '@nestjs/common';
import { EmbeddingProvider } from './embedding.provider';
import { LlmProvider } from './llm.provider';

@Module({
  providers: [EmbeddingProvider, LlmProvider],
  exports: [EmbeddingProvider, LlmProvider],
})
export class LlmModule {}
