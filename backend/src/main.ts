import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';
import { ValidationPipe } from '@nestjs/common';
import { ResponseInterceptor } from './common/interceptors/response.interceptor';
import { ConfigService } from '@nestjs/config';

async function bootstrap() {
  const app = await NestFactory.create(AppModule, { bufferLogs: true });
  app.useGlobalPipes(new ValidationPipe({ whitelist: true, transform: true }));
  app.useGlobalInterceptors(new ResponseInterceptor());

  const configService = app.get(ConfigService);
  const port = configService.get<number>('PORT') || 3001;
   app.enableCors({
    origin: configService.get<string>('FRONTEND_URL') || '*',
    credentials: true,
  });
  await app.listen(port);
  console.log(`API listening on port ${port}`);
}

bootstrap();
